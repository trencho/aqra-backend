from gc import collect
from logging import getLogger
from time import sleep

from pandas import DataFrame, json_normalize
from requests import RequestException, get

from definitions import (
    DATA_PATH,
    DATA_RAW_PATH,
    OPEN_WEATHER,
)
from processing import flatten_json, save_dataframe
from settings import get_settings

logger = getLogger(__name__)


def api_is_available() -> bool:
    """True when the OpenWeather quota lock is ABSENT, i.e. it is safe to call the API.

    Named for the polarity rather than the mechanism. It was ``check_api_lock``, which
    reads as "is it locked?" while returning the opposite -- and its two call sites were
    written with opposite polarity as a result, one of them making the hourly fetch a
    no-op for 402 days. The sibling ``check_pollutant_lock`` returns ``.exists()``, True
    meaning LOCKED, so the two ``check_*_lock`` functions meant opposite things and the
    correct call for one was the bug for the other.
    """
    return not (DATA_PATH / f"{OPEN_WEATHER}.lock").exists()


def fetch_open_weather_data(city_name: str, sensor: dict) -> bool:
    url = "https://api.openweathermap.org/data/3.0/onecall"
    sensor_position = sensor["position"].split(",")
    lat, lon = float(sensor_position[0]), float(sensor_position[1])
    units = "metric"
    exclude = "alerts,current,daily,minutely"
    token = get_settings().open_weather_token
    params = f"lat={lat}&lon={lon}&units={units}&exclude={exclude}&appid={token}"

    try:
        weather_response = get(url, params)
        if weather_response.status_code >= 400:
            lock_api()
            raise RequestException(
                f"The weather response returned content: {weather_response.text}"
            )
        hourly_data = weather_response.json()["hourly"]
        dataframe = json_normalize([flatten_json(hourly) for hourly in hourly_data])

        if len(dataframe.index) > 0:
            save_dataframe(
                dataframe,
                "weather",
                DATA_RAW_PATH / city_name / sensor["sensorId"] / "weather.csv",
                sensor["sensorId"],
            )
        del dataframe
        return True
    except Exception:
        logger.exception(
            f"Error occurred while fetching Open Weather data for {city_name} - {sensor['sensorId']}",
        )
        return False
    finally:
        collect()
        sleep(1)


def fetch_pollution_data(city_name: str, sensor: dict) -> bool:
    url = "https://api.openweathermap.org/data/2.5/air_pollution/forecast"
    sensor_position = sensor["position"].split(",")
    lat, lon = float(sensor_position[0]), float(sensor_position[1])
    token = get_settings().open_weather_token
    params = f"lat={lat}&lon={lon}&appid={token}"

    try:
        pollution_response = get(url, params)
        if pollution_response.status_code >= 400:
            lock_api()
            raise RequestException(
                f"The pollution response returned content: {pollution_response.text}"
            )
        pollution_data = pollution_response.json()["list"]
        data = []
        for pollution in pollution_data:
            pollution_dict = {"time": pollution["dt"]}
            pollution_dict.update(pollution["main"])
            pollution_dict.update(pollution["components"])
            data.append(pollution_dict)
        dataframe = DataFrame(data)

        if len(dataframe.index) > 0:
            save_dataframe(
                dataframe,
                "pollution",
                DATA_RAW_PATH / city_name / sensor["sensorId"] / "pollution.csv",
                sensor["sensorId"],
            )
        del dataframe
        return True
    except Exception:
        logger.exception(
            f"Error occurred while fetching pollution data for {city_name} - {sensor["sensorId"]}",
        )
        return False
    finally:
        collect()
        sleep(1)


def fetch_weather_data(city_name: str, sensor: dict) -> bool:
    # Both calls run whatever the first one does -- they fetch different collections and a
    # weather failure is no reason to skip pollution. Hence the two names rather than
    # `and`, which would short-circuit.
    weather_ok = fetch_open_weather_data(city_name, sensor)
    pollution_ok = fetch_pollution_data(city_name, sensor)
    return weather_ok and pollution_ok


def lock_api() -> None:
    (DATA_PATH / f"{OPEN_WEATHER}.lock").write_text("")

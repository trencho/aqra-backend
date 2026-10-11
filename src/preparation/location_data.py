from datetime import tzinfo
from json import loads
from logging import getLogger
from math import atan2, modf
from typing import Optional

from haversine import haversine_vector
from numpy import where
from pytz import country_timezones, timezone
from requests import get

from caching import cache
from definitions import CACHE_TIMEOUTS, DATA_RAW_PATH
from settings import get_settings

logger = getLogger(__name__)


def calculate_nearest_city(
    coordinates: tuple, radius_of_effect: int = 2
) -> Optional[dict]:
    cities = cache.get("cities") or read_cities()
    distances = haversine_vector(
        coordinates,
        [
            tuple(
                map(
                    float,
                    [
                        city["cityLocation"]["latitude"],
                        city["cityLocation"]["longitute"],
                    ],
                )
            )
            for city in cities
        ],
        comb=True,
    )
    min_distance = min(distances)
    return (
        cities[where(distances == min_distance)[0][0]]
        if min_distance <= radius_of_effect
        else None
    )


def calculate_nearest_sensor(
    coordinates: tuple, radius_of_effect: int = 2
) -> Optional[dict]:
    sensors = [
        sensor
        for sensor_list in [
            read_sensors(city["cityName"])
            for city in cache.get("cities") or read_cities()
        ]
        for sensor in sensor_list
    ]
    distances = haversine_vector(
        coordinates,
        [tuple(map(float, sensor["position"].split(","))) for sensor in sensors],
        comb=True,
    )
    min_distance = min(distances)
    return (
        sensors[where(distances == min_distance)[0][0]]
        if min_distance <= radius_of_effect
        else None
    )


@cache.memoize(timeout=CACHE_TIMEOUTS["1h"])
def check_city(city_name: str) -> Optional[dict]:
    for city in cache.get("cities") or read_cities():
        if str(city["cityName"]).lower() == city_name.lower():
            return city

    return None


@cache.memoize(timeout=CACHE_TIMEOUTS["1h"])
def check_country(country_code: str) -> Optional[dict]:
    for country in cache.get("countries") or read_countries():
        if str(country["countryCode"]).lower() == country_code.lower():
            return country

    return None


@cache.memoize(timeout=CACHE_TIMEOUTS["1h"])
def check_sensor(city_name: str, sensor_id: str) -> Optional[dict]:
    for sensor in read_sensors(city_name):
        if str(sensor["sensorId"]).lower() == sensor_id.lower():
            return sensor

    return None


def enabled_country_codes() -> set:
    # Defaults to every supported country; narrow with the ENABLED_COUNTRIES env var.
    return set(get_settings().enabled_countries)


def fetch_cities() -> list:
    enabled = enabled_country_codes()
    try:
        return sorted(
            [
                sort_city_coordinates(city)
                for city in get("https://pulse.eco/rest/city").json()
                if city["countryCode"] in enabled
            ],
            key=lambda i: i["cityName"],
        )
    except Exception:
        logger.exception(
            "Error fetching cities",
        )
        return []


def fetch_countries() -> list:
    try:
        return sorted(
            get("https://pulse.eco/rest/country").json(),
            key=lambda i: i["countryCode"],
        )
    except Exception:
        logger.exception(
            "Error fetching countries",
        )
        return []


def fetch_sensors(city_name: str) -> list:
    try:
        return sorted(
            [
                sensor
                for sensor in get(f"https://{city_name}.pulse.eco/rest/sensor").json()
                if sensor["status"] == "ACTIVE"
            ],
            key=lambda i: i["sensorId"],
        )
    except Exception:
        logger.exception(
            f"Error fetching sensors for city: {city_name}",
        )
        return []


@cache.memoize(timeout=CACHE_TIMEOUTS["never"])
def location_timezone(country_code: str) -> tzinfo:
    return timezone(country_timezones[country_code][0])


def read_cities() -> list:
    try:
        return loads((DATA_RAW_PATH / "cities.json").read_text())
    except OSError:
        logger.exception(
            "Error reading cities.json file",
        )
        return []


def read_countries() -> list:
    try:
        return loads((DATA_RAW_PATH / "countries.json").read_text())
    except OSError:
        logger.exception(
            "Error reading countries.json file",
        )
        return []


def read_sensors(city_name) -> list:
    try:
        return loads((DATA_RAW_PATH / city_name / "sensors.json").read_text())
    except OSError:
        logger.exception(
            f"Error reading sensors.json file for city: {city_name}",
        )
        return []


def recalculate_coordinate(val: tuple, _as: Optional[str] = None) -> float | tuple:
    """
    Accepts a coordinate as a tuple (degree, minutes, seconds) You can give only one of them (e.g., only minutes as a
    floating point number), and it will be duly recalculated into degrees, minutes and seconds. Return value can be
    specified as "deg", "min" or "sec"; the default return value is a proper coordinate tuple.
    """
    degrees, minutes, seconds = val

    minutes = (minutes or 0) + int(seconds) / 60
    seconds = seconds % 60
    degrees = (degrees or 0) + int(minutes) / 60
    minutes = minutes % 60

    degrees_fraction, degrees_integer = modf(degrees)
    minutes = minutes + degrees_fraction * 60
    degrees = degrees_integer
    minutes_fraction, minutes_integer = modf(minutes)
    seconds = seconds + minutes_fraction * 60
    minutes = minutes_integer
    if _as:
        seconds = seconds + minutes * 60 + degrees * 3600
        if _as == "sec":
            return seconds
        if _as == "min":
            return seconds / 60
        if _as == "deg":
            return seconds / 3600
    return degrees, minutes, seconds


def sort_city_coordinates(city: dict) -> dict:
    border_points = [
        tuple(border_points.values()) for border_points in city["cityBorderPoints"]
    ]
    border_points = [
        (float(latitude), float(longitude)) for latitude, longitude in border_points
    ]
    cent = (
        sum([border_point[0] for border_point in border_points]) / len(border_points),
        sum([border_point[1] for border_point in border_points]) / len(border_points),
    )
    border_points.sort(
        key=lambda border_point: atan2(
            border_point[1] - cent[1], border_point[0] - cent[0]
        )
    )
    city["cityBorderPoints"] = [
        {"latitude": border_point[0], "longitute": border_point[1]}
        for border_point in border_points
    ]

    return city

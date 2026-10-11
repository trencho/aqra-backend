"""Forecast job: publish last run's predictions, then compute the next horizon."""

from json import dumps, loads
from logging import getLogger

from caching import cache
from definitions import DATA_PROCESSED_PATH
from preparation import read_cities, read_sensors
from processing import fetch_forecast_result
from repository import get_repository
from utils import BatchTally, track_time

from .context import in_app_context

logger = getLogger(__name__)


@in_app_context
@track_time
def predict_locations() -> None:
    with BatchTally(logger, "predict_locations (publish)", "sensor") as tally:
        for city in cache.get("cities") or read_cities():
            for sensor in tally.track(read_sensors(city["cityName"])):
                file_path = (
                    DATA_PROCESSED_PATH
                    / city["cityName"]
                    / sensor["sensorId"]
                    / "predictions.json"
                )
                try:
                    get_repository().save(
                        collection_name="predictions",
                        filter={
                            "cityName": city["cityName"],
                            "sensorId": sensor["sensorId"],
                        },
                        item={
                            "data": loads(file_path.read_text()),
                            "cityName": city["cityName"],
                            "sensorId": sensor["sensorId"],
                        },
                    )
                except Exception:
                    tally.failure(
                        f"Error occurred while updating forecast values from {file_path}",
                    )

    with BatchTally(logger, "predict_locations (forecast)", "sensor") as tally:
        for city in cache.get("cities") or read_cities():
            for sensor in tally.track(read_sensors(city["cityName"])):
                try:
                    forecast_result = fetch_forecast_result(city, sensor)
                    (
                        DATA_PROCESSED_PATH
                        / city["cityName"]
                        / sensor["sensorId"]
                        / "predictions.json"
                    ).write_text(
                        dumps(list(forecast_result.values()), indent=4, default=str)
                    )
                except Exception:
                    tally.failure(
                        f"Error occurred while fetching forecast values for "
                        f"{city['cityName']} - {sensor['sensorId']}",
                    )

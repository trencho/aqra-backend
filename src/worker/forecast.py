"""Forecast job: compute each sensor's next horizon and publish it."""

from logging import getLogger

from caching import cache
from preparation import read_cities, read_sensors
from services.forecast import publish_forecast
from utils import BatchTally, track_time

from .context import in_app_context

logger = getLogger(__name__)


@in_app_context
@track_time
def predict_locations() -> None:
    with BatchTally(logger, "predict_locations", "sensor") as tally:
        for city in cache.get("cities") or read_cities():
            for sensor in tally.track(read_sensors(city["cityName"])):
                try:
                    publish_forecast(city, sensor)
                except Exception:
                    tally.failure(
                        f"Error occurred while forecasting for "
                        f"{city['cityName']} - {sensor['sensorId']}",
                    )

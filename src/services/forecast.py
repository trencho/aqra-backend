"""Publishing and reading the hourly forecast.

The worker computes a forecast per sensor and publishes it; the API reads it. Both halves used to
live inline in the scheduler job and the blueprint, and the publish ran in the wrong order: the
job first copied LAST hour's predictions.json into Mongo and only then computed the new one, so
the database fallback was always a run behind.
"""

from datetime import datetime
from json import dumps, loads
from logging import getLogger
from os import replace
from pathlib import Path
from typing import Callable
from uuid import uuid4

from definitions import DATA_PROCESSED_PATH
from processing.forecast_data import (
    epoch,
    fetch_forecast_result,
    forecast_horizon,
    utc_now,
)
from repository import get_repository

logger = getLogger(__name__)

PREDICTIONS_FILE = "predictions.json"
COLLECTION = "predictions"


def predictions_path(city_name: str, sensor_id: str) -> Path:
    return DATA_PROCESSED_PATH / city_name / sensor_id / PREDICTIONS_FILE


def _sensor_key(city: dict, sensor: dict) -> dict:
    return {"cityName": city["cityName"], "sensorId": sensor["sensorId"]}


def publish_forecast(
    city: dict, sensor: dict, clock: Callable[[], datetime] = utc_now
) -> list:
    """Compute the sensor's forecast, store it in the database, then cache it on disk.

    The database first: it is the copy every API process can read, so it must never lag the
    file. The file is written to a temporary name and renamed over the old one, so a reader
    never sees half of it.
    """
    result = fetch_forecast_result(city, sensor, clock)
    # Through JSON once, so the stored and cached copies are identical and datetimes are text.
    data = loads(dumps(list(result.values()), default=str))
    key = _sensor_key(city, sensor)
    get_repository().save(
        collection_name=COLLECTION, filter=key, item={"data": data, **key}
    )
    path = predictions_path(city["cityName"], sensor["sensorId"])
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{PREDICTIONS_FILE}.{uuid4().hex}")
    staged.write_text(dumps(data, indent=4))
    replace(staged, path)
    return data


def read_forecast(
    city: dict, sensor: dict, clock: Callable[[], datetime] = utc_now
) -> list:
    """The current forecast: the file when it starts at the upcoming hour, else the database."""
    try:
        data = loads(predictions_path(city["cityName"], sensor["sensorId"]).read_text())
        # `time` is the Unix second the worker wrote for the first hour of the horizon.
        if data[0]["time"] == epoch(forecast_horizon(clock())[0]):
            return data
    except (OSError, ValueError, IndexError, KeyError, TypeError) as error:
        # The cached file is written by the worker, so a missing, half-written or stale
        # predictions.json is routine and the database copy below is the answer. Debug rather
        # than warning for that reason, but logged: a bare `except: pass` made "no forecast
        # yet" and "the forecast file is corrupt" the same observable event.
        logger.debug(
            "Falling back to the stored forecast for %s - %s: %s",
            city["cityName"],
            sensor["sensorId"],
            error,
        )

    stored = get_repository().get(
        collection_name=COLLECTION, filter=_sensor_key(city, sensor)
    )
    return stored["data"] if stored is not None else []

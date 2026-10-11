"""Publishing and reading the hourly forecast (services/forecast.py)."""

from datetime import UTC, datetime, timedelta
from json import loads

import pytest

from processing.forecast_data import epoch, forecast_horizon
from repository import get_repository
from services import forecast as service

CITY = {"cityName": "skopje", "countryCode": "MK"}
SENSOR = {"sensorId": "1000"}
NOW = datetime(2026, 10, 11, 12, 5, tzinfo=UTC)


def _clock(moment=NOW):
    return lambda: moment


@pytest.fixture
def forecast_store(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "DATA_PROCESSED_PATH", tmp_path)
    first_hour = epoch(forecast_horizon(NOW)[0])
    monkeypatch.setattr(
        service,
        "fetch_forecast_result",
        lambda city, sensor, clock: {
            first_hour: {"time": first_hour, "dateTime": NOW, "pm10": 12.5}
        },
    )
    return tmp_path


def test_publish_stores_the_same_data_in_the_database_and_the_file(forecast_store):
    data = service.publish_forecast(CITY, SENSOR, _clock())

    stored = get_repository().get(
        collection_name="predictions", filter={"cityName": "skopje", "sensorId": "1000"}
    )
    cached = loads(service.predictions_path("skopje", "1000").read_text())
    assert stored["data"] == cached == data
    assert isinstance(data[0]["dateTime"], str)  # datetimes travel as text


def test_a_failed_database_write_leaves_no_newer_file(forecast_store, monkeypatch):
    # The database goes first, so the file can never be ahead of it.
    def refuse(**kwargs):
        raise RuntimeError("the database is gone")

    monkeypatch.setattr(get_repository(), "save", refuse)
    with pytest.raises(RuntimeError):
        service.publish_forecast(CITY, SENSOR, _clock())
    assert not service.predictions_path("skopje", "1000").exists()


def test_read_serves_the_file_while_it_starts_at_the_upcoming_hour(forecast_store):
    service.publish_forecast(CITY, SENSOR, _clock())
    assert service.read_forecast(CITY, SENSOR, _clock())[0]["pm10"] == 12.5


def test_read_falls_back_to_the_database_once_the_file_is_an_hour_old(
    forecast_store, monkeypatch
):
    service.publish_forecast(CITY, SENSOR, _clock())
    monkeypatch.setattr(
        get_repository(), "get", lambda **kwargs: {"data": ["from the database"]}
    )
    later = _clock(NOW + timedelta(hours=1))
    assert service.read_forecast(CITY, SENSOR, later) == ["from the database"]

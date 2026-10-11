"""Tests for ``api.blueprints.fetch_dataframe``, the shared history-CSV reader.

Every history and pollutants endpoint reaches its data through this one helper, and it
has three outcomes a caller has to tell apart: a frame, a 404 because the sensor has no
data yet, and a 404 because the file could not be read at all.

It used to reach the second of those by ``raise Exception`` inside its own ``try`` purely
to jump into the handler that returns the 404 — so the ordinary case of a city having no
history logged a manufactured traceback, and a real read failure was indistinguishable
from it in the log. These pin the outcomes so the restructure is not free to change them,
and pin the one thing that genuinely did change: an unexpected error now propagates
instead of being answered with a 404.
"""

from http import HTTPStatus
from json import dumps
from pathlib import Path

import pytest
from flask import Flask
from pandas import DataFrame

# Import the config package first so the api/preparation/processing chain initialises in
# order — importing an ``api`` submodule first hits a circular import.
import api.config  # noqa: F401
from api import blueprints
from api.blueprints import fetch_dataframe
from api.config.cache import cache
from processing.forecast_data import epoch, forecast_horizon, utc_now
from repository import get_repository

_CSV = "time,pm2_5\n1704067200,12.0\n1704070800,13.0\n"


@pytest.fixture
def app_context():
    # NullCache so ``@cache.memoize`` does not serve one test's frame to another; jsonify
    # needs the app context regardless.
    app = Flask(__name__)
    cache.init_app(app, {"CACHE_TYPE": "NullCache"})
    with app.app_context():
        yield


@pytest.fixture
def processed(tmp_path, monkeypatch):
    monkeypatch.setattr(blueprints, "DATA_PROCESSED_PATH", tmp_path)
    sensor_dir = tmp_path / "skopje" / "1000"
    sensor_dir.mkdir(parents=True)
    return sensor_dir


def test_returns_the_frame_when_the_csv_holds_rows(app_context, processed):
    (processed / "pollution.csv").write_text(_CSV)

    result = fetch_dataframe(Path("skopje") / "1000", "pollution")

    assert isinstance(result, DataFrame)
    assert len(result.index) == 2


def test_answers_404_when_the_csv_is_missing(app_context, processed):
    result = fetch_dataframe(Path("skopje") / "1000", "pollution")

    assert isinstance(result, tuple)
    _, status = result
    assert status == HTTPStatus.NOT_FOUND


def test_answers_404_when_the_csv_parses_but_holds_no_rows(app_context, processed):
    # The branch the ``raise Exception`` used to serve: pandas reads the file happily and
    # ``read_csv_in_chunks`` returns None because nothing survived. Same 404 as a missing
    # file, and that equivalence is the behaviour worth pinning -- only the logging differs.
    (processed / "pollution.csv").write_text("time,pm2_5\n")

    result = fetch_dataframe(Path("skopje") / "1000", "pollution")

    assert isinstance(result, tuple)
    assert result[1] == HTTPStatus.NOT_FOUND


def test_an_unexpected_error_propagates_rather_than_becoming_a_404(
    app_context, processed, monkeypatch
):
    # The point of narrowing the handler to (OSError, ValueError). A MemoryError, or a
    # TypeError from a bug in this service, is not "the data is missing for that city and
    # sensor", and answering it with that message hid the fault from anyone reading the
    # response. Only the two storage-shaped failures are still translated.
    def explode(*args, **kwargs):
        raise RuntimeError("not a storage problem")

    monkeypatch.setattr(blueprints, "read_csv_in_chunks", explode)

    with pytest.raises(RuntimeError):
        fetch_dataframe(Path("skopje") / "1000", "pollution")


# --- api.blueprints.forecast.forecast.return_sensor_forecast_results -----------------
#
# The cached-file read here was a bare ``except Exception: pass``. It is narrowed and
# logged now, so these pin both halves: which failures still fall through to the stored
# forecast, and that falling through is no longer a silent event.


@pytest.fixture
def forecast_view(tmp_path, monkeypatch):
    from api.blueprints.forecast import forecast as view
    from services import forecast as service

    monkeypatch.setattr(service, "DATA_PROCESSED_PATH", tmp_path)
    sensor_dir = tmp_path / "skopje" / "1000"
    sensor_dir.mkdir(parents=True)
    return view, sensor_dir


_FORECAST_CITY = {"cityName": "skopje", "countryCode": "MK"}
_FORECAST_SENSOR = {"sensorId": "1000", "position": "41.99,21.43"}


def _stub_repository(monkeypatch, result):
    monkeypatch.setattr(get_repository(), "get", lambda **kwargs: result, raising=False)


def test_serves_the_cached_forecast_when_it_is_for_the_upcoming_hour(
    app_context, forecast_view, monkeypatch
):
    view, sensor_dir = forecast_view
    # The integer Unix timestamp fetch_forecast_result writes into the file. Until this
    # PR the branch below compared it against an aware datetime, so it never matched and
    # this fast path was dead: the file was read and discarded on every single request.
    upcoming = epoch(forecast_horizon(utc_now())[0])
    (sensor_dir / "predictions.json").write_text(
        dumps([{"time": upcoming, "pm2_5": 12.0}])
    )
    # The stored copy must not be consulted at all when the file is current.
    _stub_repository(monkeypatch, {"data": ["from the database"]})

    forecast = view.return_sensor_forecast_results(_FORECAST_CITY, _FORECAST_SENSOR)

    assert forecast["data"][0]["pm2_5"] == 12.0
    assert forecast["latitude"] == pytest.approx(41.99)


def test_falls_back_to_the_stored_forecast_when_the_cached_file_is_stale(
    app_context, forecast_view, monkeypatch
):
    # The other half of the fix. Serving the file is only correct while it is for the
    # upcoming hour; a file left over from an earlier run must still lose to the database.
    # Without this the "compare the same thing the writer produced" change could be
    # satisfied by comparing nothing at all.
    view, sensor_dir = forecast_view
    stale = epoch(forecast_horizon(utc_now())[0]) - 3600
    (sensor_dir / "predictions.json").write_text(
        dumps([{"time": stale, "pm2_5": 12.0}])
    )
    _stub_repository(monkeypatch, {"data": ["from the database"]})

    forecast = view.return_sensor_forecast_results(_FORECAST_CITY, _FORECAST_SENSOR)

    assert forecast["data"] == ["from the database"]


def test_falls_back_to_the_stored_forecast_when_the_cached_file_is_missing(
    app_context, forecast_view, monkeypatch, caplog
):
    view, _ = forecast_view
    _stub_repository(monkeypatch, {"data": ["from the database"]})

    with caplog.at_level("DEBUG", logger="services.forecast"):
        forecast = view.return_sensor_forecast_results(_FORECAST_CITY, _FORECAST_SENSOR)

    assert forecast["data"] == ["from the database"]
    # The fallback is recorded. Before this it was `pass`, so "no forecast has been
    # generated yet" and "the forecast file is corrupt" were the same observable event.
    assert any("skopje" in record.message for record in caplog.records)


def test_falls_back_to_the_stored_forecast_when_the_cached_file_is_corrupt(
    app_context, forecast_view, monkeypatch, caplog
):
    view, sensor_dir = forecast_view
    (sensor_dir / "predictions.json").write_text("{not json")
    _stub_repository(monkeypatch, {"data": ["from the database"]})

    with caplog.at_level("DEBUG", logger="services.forecast"):
        forecast = view.return_sensor_forecast_results(_FORECAST_CITY, _FORECAST_SENSOR)

    assert forecast["data"] == ["from the database"]
    assert any("skopje" in record.message for record in caplog.records)


def test_returns_an_empty_forecast_when_neither_source_has_one(
    app_context, forecast_view, monkeypatch
):
    view, _ = forecast_view
    _stub_repository(monkeypatch, None)

    forecast = view.return_sensor_forecast_results(_FORECAST_CITY, _FORECAST_SENSOR)

    assert forecast["data"] == []

"""Hermetic regression tests for the missing-data path (no network, no seeded data).

Guards a real bug: ``fetch_dataframe`` returns a ``(Response, 404)`` tuple when a
processed CSV is absent, and the pollutants/history helpers must surface that 404
rather than crash. Previously the callers guarded with ``isinstance(df, Response)``,
which let the *tuple* through and blew up on ``df.columns`` / ``df.loc`` — a 500 for
every sensor without generated data.
"""

from pathlib import Path

import pytest
from flask import Flask, Response

# Import the package before pulling names out of ``api.blueprints`` so the config
# package initialises first (importing ``api.blueprints`` first is a circular import).
import api.config  # noqa: F401
from api.blueprints import fetch_dataframe
from api.blueprints.history.history import return_historical_data
from api.blueprints.pollutants.pollutants import fetch_measurements
from api.config.cache import cache


@pytest.fixture
def app_context():
    app = Flask(__name__)
    cache.init_app(app, {"CACHE_TYPE": "NullCache"})
    with app.app_context():
        yield


def _assert_404_tuple(result) -> None:
    assert isinstance(
        result, tuple
    ), f"expected a (Response, status) tuple, got {result!r}"
    response, status = result
    assert status == 404
    assert isinstance(response, Response)


def test_fetch_dataframe_missing_csv_returns_404_tuple(app_context):
    _assert_404_tuple(fetch_dataframe(Path("no-city") / "no-sensor", "pollution"))


def test_pollutant_measurements_missing_data_returns_404(app_context):
    _assert_404_tuple(fetch_measurements("no-city", "no-sensor"))


def test_historical_data_missing_data_returns_404(app_context):
    sensor = {"sensorId": "no-sensor", "position": "41.0,21.0"}
    _assert_404_tuple(
        return_historical_data("no-city", sensor, "pollution", 0, 9_999_999_999)
    )


def test_missing_data_message_names_the_collection_not_history(app_context):
    # The pollutants endpoint shares this 404, and it used to tell callers that
    # "historical data" was missing when they had asked which pollutants a sensor reports.
    response, _ = fetch_dataframe(Path("no-city") / "no-sensor", "weather")
    message = response.get_json()["error_message"]
    assert "historical" not in message
    assert "weather data is missing" in message

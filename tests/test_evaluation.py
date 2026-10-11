"""Hermetic tests for the forecast-quality (evaluation) blueprint.

Publishes a run whose manifest carries the held-out metrics, through the registry
under a temp tree, and exercises the handlers directly (tiny Flask + NullCache app
context), with the location lookups patched — the ``test_missing_data.py`` pattern.
"""

from unittest.mock import patch

import numpy as np
import pytest
from flask import Flask, Response
from pandas import DataFrame

# Import the config package first so the api.blueprints chain initialises in order.
import api.config  # noqa: F401
from api.blueprints.evaluation import evaluation
from api.config.cache import cache
from api.config.converters import PollutantType
from models import make_model
from processing.feature_scaling import fit_scaler

_MOD = "api.blueprints.evaluation.evaluation"


@pytest.fixture
def app_context():
    app = Flask(__name__)
    cache.init_app(app, {"CACHE_TYPE": "NullCache"})
    with app.app_context():
        yield


@pytest.fixture
def seeded(publish_model):
    return publish_model


def _seed(publish, city, sensor, pollutant, model, row):
    mae, mape, mse, rmse = (float(value) if value else None for value in row.split(","))
    trained = make_model(model)
    trained.train(np.arange(8.0).reshape(-1, 1), np.arange(8.0))
    _, scaler = fit_scaler(DataFrame({"a": np.arange(8.0)}))
    publish(
        city,
        sensor,
        pollutant,
        trained,
        ["a"],
        scaler,
        metrics={
            "Mean Absolute Error": mae,
            "Mean Absolute Percentage Error": mape,
            "Mean Squared Error": mse,
            "Root Mean Squared Error": rmse,
        },
    )


def test_read_best_model_evaluation_returns_metrics(seeded):
    _seed(
        seeded, "skopje", "1000", "pm2_5", "LinearRegressionModel", "1.5,10.0,4.0,2.0"
    )
    result = evaluation._read_best_model_evaluation("skopje", "1000", "pm2_5")
    assert result["model"] == "LinearRegressionModel"
    assert result["metrics"]["Mean Absolute Error"] == 1.5
    assert result["metrics"]["Root Mean Squared Error"] == 2.0


def test_read_best_model_evaluation_no_model_returns_none(seeded):
    assert evaluation._read_best_model_evaluation("nowhere", "0", "pm2_5") is None


def test_pollutant_evaluation_unknown_city_returns_404(app_context, seeded):
    with patch(f"{_MOD}.check_city", return_value=None):
        result = evaluation.fetch_pollutant_evaluation(
            "nowhere", "1", PollutantType.PM2_5
        )
    assert isinstance(result, tuple) and result[1] == 404


def test_pollutant_evaluation_no_model_returns_404(app_context, seeded):
    with patch(f"{_MOD}.check_city", return_value={"cityName": "skopje"}), patch(
        f"{_MOD}.check_sensor", return_value={"sensorId": "1000"}
    ):
        result = evaluation.fetch_pollutant_evaluation(
            "skopje", "1000", PollutantType.PM2_5
        )
    assert isinstance(result, tuple) and result[1] == 404


def test_pollutant_evaluation_returns_metrics(app_context, seeded):
    _seed(seeded, "skopje", "1000", "pm2_5", "XGBoostRegressionModel", "2.0,,9.0,3.0")
    with patch(f"{_MOD}.check_city", return_value={"cityName": "skopje"}), patch(
        f"{_MOD}.check_sensor", return_value={"sensorId": "1000"}
    ):
        response = evaluation.fetch_pollutant_evaluation(
            "skopje", "1000", PollutantType.PM2_5
        )
    assert isinstance(response, Response)
    body = response.get_json()
    assert body["model"] == "XGBoostRegressionModel"
    assert body["metrics"]["Mean Absolute Error"] == 2.0
    assert body["metrics"]["Mean Absolute Percentage Error"] is None  # empty -> None


def test_sensor_evaluation_aggregates_pollutants(app_context, seeded):
    _seed(
        seeded, "skopje", "1000", "pm2_5", "LinearRegressionModel", "1.5,10.0,4.0,2.0"
    )
    _seed(
        seeded,
        "skopje",
        "1000",
        "no2",
        "RandomForestRegressionModel",
        "0.5,5.0,1.0,1.0",
    )
    with patch(f"{_MOD}.check_city", return_value={"cityName": "skopje"}), patch(
        f"{_MOD}.check_sensor", return_value={"sensorId": "1000"}
    ):
        response = evaluation.fetch_sensor_evaluation("skopje", "1000")
    body = response.get_json()
    assert set(body) == {"pm2_5", "no2"}
    assert body["no2"]["model"] == "RandomForestRegressionModel"

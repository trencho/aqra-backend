"""Integration tests for the forecast computation path (``processing/forecast_data``).

Unlike the unit tests, these wire the whole pipeline together with real seeded
artifacts under a temp tree: a published model plus processed weather/pollution CSVs.
``fetch_forecast_result`` exercises the single per-sensor read -> model loading -> the
25-step recursive forecast loop -> feature generation/scaling -> prediction, and
aggregates the per-pollutant series into the timestamp dict.

The model is trained on time-derived features (hour/month cyclic encodings), which are
defined for future steps from the datetime index alone — so the recursion yields real
predictions without needing future weather rows (which a static fixture can't provide).
"""

import numpy as np
import pytest
from flask import Flask
from pandas import (
    DataFrame,
    DatetimeIndex,
    Series,
    Timedelta,
    date_range,
    read_csv,
    to_datetime,
)

# Import the config package first so the api/preparation/processing chain initialises
# in order — importing a ``processing`` submodule first hits a circular import.
import api.config  # noqa: F401
from api.config.cache import cache
from models import make_model
from processing import forecast_data
from processing.feature_generation import generate_features
from processing.feature_scaling import fit_scaler
from processing.normalize_data import current_hour

_TIME_FEATURES = ["hour_cos", "hour_sin", "month_cos", "month_sin"]


@pytest.fixture
def app_context():
    app = Flask(__name__)
    cache.init_app(app, {"CACHE_TYPE": "NullCache"})
    with app.app_context():
        yield


@pytest.fixture
def forecast_env(tmp_path, monkeypatch, publish_model):
    """Seed processed CSVs + a saved model for skopje/1000/pm2_5 and point
    forecast_data at the temp tree."""
    processed = tmp_path / "processed"
    models_path = publish_model.models_path
    sensor_dir = processed / "skopje" / "1000"
    sensor_dir.mkdir(parents=True)

    n = 400
    index = date_range(end=current_hour(), periods=n, freq="h")
    # Epoch seconds, derived by asking for seconds rather than by dividing nanoseconds.
    # `astype("int64") // 10**9` assumed a datetime64[ns] index; pandas 3 builds date_range at
    # MICROSECOND resolution, so that expression was 1000x too small and every fixture timestamp
    # landed in January 1970. The 52-week window in recursive_forecast then discarded the whole
    # frame, and the forecast loop ran against a series it had fabricated from its own seed --
    # so this suite exercised a synthetic path and still passed on "not None".
    seconds = index.astype("datetime64[s]").astype("int64")
    rng = np.random.RandomState(1)
    temperature = 10 + 5 * rng.rand(n)
    # Positive, hourly-seasonal target so predictions stay in a sane, positive range.
    pm2_5 = 20 + 5 * np.sin(np.arange(n) * 2 * np.pi / 24) + rng.rand(n)
    DataFrame({"time": seconds, "temp": temperature}).to_csv(
        sensor_dir / "weather.csv", index=False
    )
    DataFrame({"time": seconds, "pm2_5": pm2_5, "pm10": pm2_5 * 1.5}).to_csv(
        sensor_dir / "pollution.csv", index=False
    )

    target = Series(pm2_5, index=index)
    features = generate_features(target, 25)[_TIME_FEATURES].dropna()
    model = make_model("LinearRegressionModel")
    model.train(features, target.loc[features.index])
    # The scaler is fitted on the same features the model was trained on, and saved beside it
    # with the manifest that ties the three together. Without pipeline.json the loader refuses
    # the directory, which is the behaviour that retires every pre-scaler artefact.
    _, scaler = fit_scaler(features)
    publish_model("skopje", "1000", "pm2_5", model, _TIME_FEATURES, scaler)

    monkeypatch.setattr(forecast_data, "DATA_PROCESSED_PATH", processed)
    return processed, models_path


def _series(result, pollutant):
    times = sorted(result)
    return Series(
        [result[time].get(pollutant) for time in times],
        index=to_datetime(times, unit="s"),
        dtype="float64",
    )


def test_fetch_forecast_result_produces_a_finite_forecast(app_context, forecast_env):
    result = forecast_data.fetch_forecast_result(
        {"cityName": "skopje", "countryCode": "MK"}, {"sensorId": "1000"}
    )
    series = _series(result, "pm2_5")

    # 25 horizon steps, all kept: the opening forecast is no longer seeded with a
    # fabricated 0.0, so there is nothing to discard.
    assert len(series) == 25
    assert isinstance(series.index, DatetimeIndex)
    assert series.index[1] - series.index[0] == Timedelta(hours=1)
    # Real predictions flowed through the pipeline (not the all-NaN degenerate path),
    # and they land in the trained target's positive range.
    assert series.notna().any()
    assert (series.dropna() > 0).all()


def test_a_pollutant_without_a_model_is_left_out(app_context, forecast_env):
    # No model was published for pm10, though the sensor reports it.
    result = forecast_data.fetch_forecast_result(
        {"cityName": "skopje", "countryCode": "MK"}, {"sensorId": "1000"}
    )
    assert all("pm10" not in entry for entry in result.values())


def test_a_model_trained_on_lags_forecasts_with_those_lags(
    app_context, forecast_env, publish_model
):
    """The forecast loop used to re-select lags from the last 51 values.

    That picks a different set from training, and any lag the model needed and the rerun
    dropped raised at `features[model_features]`, so every hour came back NaN. Lags 7, 9 and
    10 are chosen here because a re-selection over this series does not produce all of them.
    """
    processed, _ = forecast_env
    pollution = read_csv(processed / "skopje" / "1000" / "pollution.csv")
    target = Series(
        pollution["pm2_5"].to_numpy(),
        index=to_datetime(pollution["time"], unit="s"),
    )
    lag_names = ["lag_7", "lag_9", "lag_10"]
    features = generate_features(target, fixed_lags=[7, 9, 10])[
        lag_names + _TIME_FEATURES
    ].dropna()
    model = make_model("LinearRegressionModel")
    model.train(features, target.loc[features.index])
    _, scaler = fit_scaler(features)
    publish_model("skopje", "1000", "pm2_5", model, lag_names + _TIME_FEATURES, scaler)

    result = forecast_data.fetch_forecast_result(
        {"cityName": "skopje", "countryCode": "MK"}, {"sensorId": "1000"}
    )
    assert _series(result, "pm2_5").notna().all()


def test_fetch_forecast_result_aggregates_by_timestamp(app_context, forecast_env):
    result = forecast_data.fetch_forecast_result(
        {"cityName": "skopje", "countryCode": "MK"}, {"sensorId": "1000"}
    )

    assert len(result) == 25
    entry = result[sorted(result)[0]]
    assert {"dateTime", "time", "pm2_5"} <= set(entry)
    assert entry["pm2_5"] is not None

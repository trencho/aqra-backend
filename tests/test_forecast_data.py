"""Hermetic tests for model loading in ``processing/forecast_data``.

``load_regression_model`` reads the registry's current run, so it also guards the
LightGBM ``NotFittedError`` regression: a published model must be reloadable and able
to predict. The ``publish_model`` fixture (conftest.py) points the registry at a temp
tree, and a NullCache app context satisfies the remaining ``@cache.memoize`` decorators.
"""

from json import dumps, loads
from pathlib import Path

import numpy as np
import pytest
from flask import Flask
from pandas import DataFrame, date_range

# Import the config package first so the api/preparation/processing chain initialises
# in order — importing a ``processing`` submodule first hits a circular import.
import api.config  # noqa: F401
from api.config.cache import cache
from definitions import PIPELINE_SCHEMA
from modeling.train_model import save_pipeline
from models import make_model, registry
from processing import forecast_data
from processing.feature_scaling import fit_scaler


@pytest.fixture
def app_context():
    app = Flask(__name__)
    cache.init_app(app, {"CACHE_TYPE": "NullCache"})
    with app.app_context():
        yield


def test_load_regression_model_roundtrips_lightgbm(app_context, publish_model):
    # Regression for the LightGBM save/load bug: the reloaded model must predict
    # without raising NotFittedError.
    rng = np.random.RandomState(0)
    x = rng.rand(100, 3)
    y = x @ np.array([1.0, 2.0, -1.0])
    model = make_model("LightGBMRegressionModel")
    model.set_params(verbose=-1)
    model.train(x, y)
    _, scaler = fit_scaler(DataFrame(x, columns=["a", "b", "c"]))
    publish_model("skopje", "1000", "pm2_5", model, ["a", "b", "c"], scaler)

    result = forecast_data.load_regression_model("skopje", "1000", "pm2_5")
    assert result is not None
    loaded_model, features, loaded_scaler = result
    assert features == ["a", "b", "c"]
    assert loaded_scaler is not None
    predictions = loaded_model.predict(x)  # must not raise NotFittedError
    assert len(predictions) == len(y)


def test_load_regression_model_missing_returns_none(app_context, publish_model):
    assert forecast_data.load_regression_model("nope", "nope", "pm2_5") is None


def test_recursive_forecast_logs_each_step_it_could_not_predict(
    app_context, tmp_path, monkeypatch, caplog
):
    """A step that fails yields NaN, and now says so.

    The handler around the per-step prediction stays deliberately broad: each hour is
    forecast from the hour before it, so abandoning the loop on the first failure would
    throw away the rest of the horizon too, and NaN is already how this function reports
    "no value for this hour".

    What it lacked was any record. A model that failed on every single step returned an
    all-NaN series, which reads exactly like a sensor with nothing to forecast -- so a
    broken model stayed invisible for as long as nobody compared it against a working one.
    """
    frame = DataFrame(
        {"pm2_5": [10.0, 11.0, 12.0]},
        index=date_range("2026-01-01", periods=3, freq="1h"),
    )
    monkeypatch.setattr(
        forecast_data, "fetch_summary_dataframe", lambda *args, **kwargs: frame
    )
    monkeypatch.setattr(forecast_data, "DATA_PROCESSED_PATH", tmp_path)

    def unavailable(*args, **kwargs):
        raise RuntimeError("upstream feature service is down")

    monkeypatch.setattr(forecast_data, "forecast_sensor", unavailable)

    with caplog.at_level("ERROR", logger=forecast_data.__name__):
        result = forecast_data.recursive_forecast(
            "skopje",
            "1000",
            "pm2_5",
            model=None,
            model_features=[],
            scaler=None,
            n_steps=3,
        )

    # Still a full-length series of NaN: the loop runs to the end of the horizon.
    # n_steps values, not n_steps - 1: the loop no longer fabricates its opening row, so the
    # first hour is a real forecast and is no longer dropped.
    assert len(result.index) == 3
    assert bool(result.isnull().all())
    # One record per failed step, each naming the sensor and the hour, and each carrying
    # the original traceback rather than just the fact that something went wrong.
    assert len(caplog.records) == 3
    assert all("skopje" in record.message for record in caplog.records)
    assert all("1000" in record.message for record in caplog.records)
    assert all(record.exc_info is not None for record in caplog.records)


def test_a_pre_registry_model_directory_is_refused(app_context, publish_model):
    """Every artefact trained before the registry is retired by this check.

    It matters more here than it looks: the cluster this deploys to has been unreachable since
    2026-08-26, so purging models/ by hand is not currently possible. Those flat directories
    have no `current` pointer, so the loader finds nothing and the worker retrains them.
    """
    model_dir = publish_model.models_path / "skopje" / "1000" / "pm2_5"
    model_dir.mkdir(parents=True)
    (model_dir / "LightGBMRegressionModel.mdl").write_bytes(b"not read")
    (model_dir / "selected_features.json").write_text(dumps(["a"]))
    (model_dir / "pipeline.json").write_text(dumps({"schema": 1, "features": ["a"]}))

    assert forecast_data.load_regression_model("skopje", "1000", "pm2_5") is None


def test_a_run_from_an_older_schema_is_refused(tmp_path):
    run_dir = tmp_path / "run"
    model = make_model("LinearRegressionModel")
    model.train(np.arange(4.0).reshape(-1, 1), np.arange(4.0))
    run_dir.mkdir()
    model.save(run_dir)
    _, scaler = fit_scaler(DataFrame({"a": [0.0, 1.0, 2.0, 3.0]}))
    save_pipeline(run_dir, ["a"], scaler, "LinearRegressionModel")
    manifest = loads((run_dir / "pipeline.json").read_text())
    manifest["schema"] = PIPELINE_SCHEMA - 1
    (run_dir / "pipeline.json").write_text(dumps(manifest))

    assert registry.load_run(run_dir) is None


def test_the_manifest_names_the_model_so_a_stale_mdl_is_never_loaded(
    app_context, publish_model
):
    """The loader used to take glob("*.mdl")[0], so a previous winner's file could be served.

    A run directory holds one model and the manifest names its class; a stray file beside
    it is ignored.
    """
    model = make_model("LinearRegressionModel")
    model.train(np.arange(8.0).reshape(-1, 1), np.arange(8.0))
    _, scaler = fit_scaler(DataFrame({"a": np.arange(8.0)}))
    run_dir = publish_model("skopje", "1000", "pm2_5", model, ["a"], scaler)
    (run_dir / "AnOldWinner.mdl").write_bytes(b"garbage")
    registry.load_run.cache_clear()

    loaded_model, _, _ = forecast_data.load_regression_model("skopje", "1000", "pm2_5")
    assert type(loaded_model).__name__ == "LinearRegressionModel"


def test_a_scaler_is_never_saved_as_a_model(tmp_path):
    _, scaler = fit_scaler(DataFrame({"a": [1.0, 2.0]}))
    save_pipeline(tmp_path, ["a"], scaler, "LinearRegressionModel")

    assert not list(tmp_path.glob("scaler*.mdl"))
    assert (tmp_path / "scaler.pkl").exists()


def test_disagreeing_artefacts_are_refused():
    class Scaler:
        feature_names_in_ = np.array(["a", "b"])

    manifest = {"schema": PIPELINE_SCHEMA, "features": ["a", "b"]}

    with pytest.raises(forecast_data.ModelArtifactMismatch, match="feature list"):
        forecast_data.verify_pipeline(Path("d"), manifest, object(), ["a"], Scaler())

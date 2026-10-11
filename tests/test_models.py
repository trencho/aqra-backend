"""Hermetic smoke tests for the ML regression model classes (``src/models``).

For every enabled model: instantiate via ``make_model``, train on a tiny synthetic
regression, predict (right length, finite), and round-trip through ``save``/``load``
(the reloaded model predicts identically). No data, network, or repository needed.

Guards a real bug: ``LightGBMRegressionModel``'s save/load override restored only the
raw ``Booster``, leaving the sklearn wrapper unfitted, so ``predict`` raised
``NotFittedError`` after every reload — which broke LightGBM forecasts, since
``forecast_data.py`` loads the saved model before predicting.
"""

import numpy as np
import pytest

# Import the config package first so the api/preparation/processing chain initialises
# in order — importing a package submodule first can hit a circular import.
import api.config  # noqa: F401
import models
from models import make_model


@pytest.fixture(scope="module")
def training_data():
    rng = np.random.RandomState(0)
    x = rng.rand(100, 4)
    y = x @ np.array([1.0, 2.0, -1.0, 0.5]) + 0.1 * rng.rand(100)
    return x, y


@pytest.mark.parametrize("model_name", models.__all__)
def test_model_train_predict(model_name, training_data):
    x, y = training_data
    model = make_model(model_name)
    model.train(x, y)
    predictions = model.predict(x)
    assert len(predictions) == len(y)
    assert np.all(np.isfinite(predictions))


@pytest.mark.parametrize("model_name", models.__all__)
def test_model_save_load_roundtrip(model_name, training_data, tmp_path):
    x, y = training_data
    model = make_model(model_name)
    model.train(x, y)
    expected = model.predict(x)

    model.save(tmp_path)
    reloaded = make_model(model_name)
    reloaded.load(tmp_path)
    assert np.allclose(reloaded.predict(x), expected)


def test_make_model_unknown_raises():
    with pytest.raises(Exception):
        make_model("NoSuchModel")

from datetime import datetime, timedelta
from logging import getLogger
from math import isnan, nan
from typing import Optional

from pandas import DataFrame, Series, concat, date_range

from caching import cache
from definitions import CACHE_TIMEOUTS, DATA_PROCESSED_PATH, POLLUTANTS
from models.base_regression_model import BaseRegressionModel
from models.registry import (  # noqa: F401  (re-exported for callers and tests)
    ModelArtifactMismatch,
    ModelKey,
    load_current,
    verify_pipeline,
)

from .feature_generation import encode_categorical_data, generate_features
from .feature_scaling import apply_scaler
from .handle_data import fetch_summary_dataframe, read_csv_in_chunks
from .normalize_data import current_hour, next_hour

logger = getLogger(__name__)


def _location_timezone():
    """Deferred import, and the deferral is the point.

    `preparation.weather_data` imports from `processing`, and this module is imported by
    `processing/__init__`, so a module-level `from preparation import location_timezone` closes a
    cycle between two sibling packages. Importing it at call time breaks that, which is what makes
    `modeling` and `processing` importable - and therefore testable - without booting Flask.
    """
    from preparation import location_timezone

    return location_timezone


FORECAST_PERIOD = "1h"
FORECAST_STEPS = 25


def fetch_forecast_result(city: dict, sensor: dict) -> dict:
    forecast_result = {}
    for pollutant in POLLUTANTS:
        if (
            predictions := forecast_city_sensor(
                city["cityName"], sensor["sensorId"], pollutant
            )
        ) is None:
            continue

        for index, value in predictions.items():
            timestamp = int(index.timestamp())
            timestamp_dict = forecast_result.get(timestamp, {})
            date_time = datetime.fromtimestamp(
                timestamp, _location_timezone()(city["countryCode"])
            )
            timestamp_dict.update(
                {
                    "dateTime": date_time,
                    "time": timestamp,
                    pollutant: None if isnan(value) else value,
                }
            )
            forecast_result.update({timestamp: timestamp_dict})

    return forecast_result


@cache.memoize(timeout=CACHE_TIMEOUTS["1h"])
def forecast_city_sensor(
    city_name: str, sensor_id: str, pollutant: str
) -> Optional[Series]:
    if (load_model := load_regression_model(city_name, sensor_id, pollutant)) is None:
        return None

    model, model_features, scaler = load_model
    return recursive_forecast(
        city_name, sensor_id, pollutant, model, model_features, scaler
    )


@cache.memoize(timeout=CACHE_TIMEOUTS["1h"])
def forecast_sensor(city_name: str, sensor_id: str, timestamp: int) -> dict:
    dataframe = read_csv_in_chunks(
        DATA_PROCESSED_PATH / city_name / sensor_id / "weather.csv"
    )
    dataframe = dataframe.loc[dataframe["time"] == timestamp]
    if len(dataframe.index) > 0:
        return dataframe.to_dict("records")[0]

    return {}


def load_regression_model(
    city_name: str, sensor_id: str, pollutant: str
) -> Optional[tuple]:
    """The current run's model, features and scaler, or None if nothing servable exists.

    None also covers every artefact trained before the registry: those directories have no
    `current` pointer and no schema-2 manifest, so the worker retrains them instead.
    """
    return load_current(ModelKey(city_name, sensor_id, pollutant))


@cache.memoize(timeout=CACHE_TIMEOUTS["1h"])
def recursive_forecast(
    city_name: str,
    sensor_id: str,
    pollutant: str,
    model: BaseRegressionModel,
    model_features: list,
    scaler,
    lags: int = FORECAST_STEPS,
    n_steps: int = FORECAST_STEPS,
    step: str = FORECAST_PERIOD,
) -> Series:
    """Multistep recursive forecasting using the input time series data and a pre-trained machine learning model

    Parameters
    ----------
    city_name: The name of the city where the sensor is located
    sensor_id: The ID of the sensor to fetch weather data
    pollutant: The pollutant that is used as a forecasting target
    model: An already trained machine learning model implementing the scikit-learn interface
    model_features: Selected model features for forecasting
    lags: List of lags used for training the model
    n_steps: Number of time periods in the forecasting horizon
    step: The period of forecasting

    Returns
    -------
    forecast_values: pd.Series with forecasted values indexed by forecast horizon dates
    """

    upcoming_hour = next_hour(current_hour())
    forecast_range = date_range(upcoming_hour, periods=n_steps, freq=step)

    dataframe = fetch_summary_dataframe(
        DATA_PROCESSED_PATH / city_name / sensor_id, index_col="time"
    )
    dataframe = dataframe.loc[datetime.now() - timedelta(weeks=52) : datetime.now()]
    # Check AFTER the window is applied, not before. The guard used to sit above this slice,
    # so a frame whose rows all fall outside the window reached the loop empty -- and the loop
    # then built its own series out of the placeholder it was seeding, forecasting from data
    # that does not exist. An empty window is "nothing to forecast", the same as an empty file.
    if len(dataframe.index) == 0:
        return Series(dtype="float64")

    target = dataframe[pollutant].tail(lags * 2 + 1)

    forecasted_values = []
    for date in forecast_range:
        # Predict the value AT `date` from the features at the hour BEFORE it, which is the
        # pairing the model is trained on: x(t) -> y(t+1).
        #
        # This loop used to append a placeholder row AT `date` first and compute the lag
        # features there, one step out of step with training, and it seeded that placeholder
        # with 0.0 on the first iteration - a value the series never takes - so the opening
        # forecast was made from a fabricated lag. That is why the old code then dropped its
        # own first result. With the pairing corrected, that hour is a real forecast and is
        # kept, so this returns n_steps values rather than n_steps - 1.
        #
        # The exogenous half was already aligned this way: the weather lookup below has always
        # used `date - 1h`. Only the target side was wrong.
        previous_hour = date - timedelta(hours=1)
        timestamp = int(previous_hour.timestamp())
        try:
            data = forecast_sensor(city_name, sensor_id, timestamp)
            features = DataFrame(data, index=[previous_hour])
            features = concat([dataframe, features])
            features = features[~features.index.duplicated(keep="last")]
            features = features.join(generate_features(target, lags), how="inner")
            features = features[model_features]
            encode_categorical_data(features)
            # Transform with the TRAINING scaler, not a fresh one fitted on this frame.
            features = apply_scaler(features, scaler)
            features = features.tail(1)
            predictions = model.predict(features)
            prediction = predictions[-1]
            forecasted_values.append(prediction if prediction >= 0 else nan)
        except Exception:
            # Deliberately broad, and it stays broad: each iteration forecasts one hour from
            # the hour before it, so abandoning the loop on the first bad step would throw
            # away the rest of the horizon as well. NaN is already how this function says
            # "no value for this hour", and the caller drops those.
            #
            # What was missing is the record. Without this log a model that failed on every
            # single step returned an all-NaN series that reads exactly like a sensor with
            # nothing to forecast, so a broken model was invisible for as long as nobody
            # compared it against a working one.
            logger.exception(
                f"Could not forecast {pollutant} for {city_name} - {sensor_id} at {date}",
            )
            forecasted_values.append(nan)
        # Feed the prediction back in at `date`, so the next step's lags can see it.
        target = concat([target, Series(forecasted_values[-1], [date])])
        target = target.tail(lags * 2 + 1)

    return Series(forecasted_values, forecast_range)

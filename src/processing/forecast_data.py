from datetime import UTC, datetime, timedelta
from logging import getLogger
from math import isnan, nan
from typing import Callable, Optional

from pandas import DataFrame, DatetimeIndex, Series, concat, date_range

from definitions import DATA_PROCESSED_PATH, POLLUTANTS
from models.base_regression_model import BaseRegressionModel
from models.registry import (  # noqa: F401  (re-exported for callers and tests)
    ModelArtifactMismatch,
    ModelKey,
    load_current,
    verify_pipeline,
)

from .feature_generation import encode_categorical_data, generate_features, lags_of
from .feature_scaling import apply_scaler
from .handle_data import fetch_summary_dataframe, read_csv_in_chunks

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
HISTORY_WINDOW = timedelta(weeks=52)


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_naive_utc(moment: datetime) -> datetime:
    """The processed CSVs index by naive UTC (`to_datetime(unit="s")`), so compare in that."""
    return moment.astimezone(UTC).replace(tzinfo=None)


def epoch(moment: datetime) -> int:
    """Unix seconds for a naive-UTC or aware datetime."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp())


def forecast_horizon(now: datetime, n_steps: int = FORECAST_STEPS) -> DatetimeIndex:
    """The next `n_steps` whole hours after `now`, as naive UTC.

    This used `datetime.now()`, the container's LOCAL time (TZ=Europe/Skopje), against data
    indexed in UTC, so the history window ended up to two hours in the future and pulled in
    OpenWeather's forecast rows as if they had been observed.
    """
    start = as_naive_utc(now).replace(minute=0, second=0, microsecond=0)
    return date_range(start + timedelta(hours=1), periods=n_steps, freq=FORECAST_PERIOD)


def load_sensor_data(
    city_name: str, sensor_id: str, now: datetime
) -> tuple[DataFrame, dict]:
    """The sensor's last year of readings, and its weather rows keyed by Unix time.

    Read once per sensor per run. The forecast used to read both CSVs once per pollutant, nine
    times, and to reread the whole weather.csv for every hour of the horizon.
    """
    data_path = DATA_PROCESSED_PATH / city_name / sensor_id
    dataframe = fetch_summary_dataframe(data_path, index_col="time")
    upper = as_naive_utc(now)
    dataframe = dataframe.loc[upper - HISTORY_WINDOW : upper]
    weather = read_csv_in_chunks(data_path / "weather.csv")
    weather_by_time = (
        {}
        if weather is None
        else {int(row["time"]): row for row in weather.to_dict("records")}
    )
    return dataframe, weather_by_time


def load_regression_model(
    city_name: str, sensor_id: str, pollutant: str
) -> Optional[tuple]:
    """The current run's model, features and scaler, or None if nothing servable exists.

    None also covers every artefact trained before the registry: those directories have no
    `current` pointer and no schema-2 manifest, so the worker retrains them instead.
    """
    return load_current(ModelKey(city_name, sensor_id, pollutant))


def fetch_forecast_result(
    city: dict, sensor: dict, clock: Callable[[], datetime] = utc_now
) -> dict:
    city_name, sensor_id = city["cityName"], sensor["sensorId"]
    now = clock()
    dataframe, weather_by_time = load_sensor_data(city_name, sensor_id, now)
    # Check AFTER the window is applied: an empty window is "nothing to forecast", the same as
    # an empty file. With the guard above the slice, an out-of-window frame reached the loop.
    if len(dataframe.index) == 0:
        return {}

    horizon = forecast_horizon(now)
    timezone = _location_timezone()(city["countryCode"])
    forecast_result = {}
    for pollutant in POLLUTANTS:
        if pollutant not in dataframe.columns:
            continue
        if (loaded := load_regression_model(city_name, sensor_id, pollutant)) is None:
            continue
        model, model_features, scaler = loaded
        predictions = recursive_forecast(
            dataframe,
            weather_by_time,
            pollutant,
            model,
            model_features,
            scaler,
            horizon,
            label=f"{city_name} - {sensor_id}",
        )

        for index, value in predictions.items():
            timestamp = epoch(index)
            timestamp_dict = forecast_result.setdefault(timestamp, {})
            timestamp_dict.update(
                {
                    "dateTime": datetime.fromtimestamp(timestamp, timezone),
                    "time": timestamp,
                    pollutant: None if isnan(value) else value,
                }
            )

    return forecast_result


def recursive_forecast(
    dataframe: DataFrame,
    weather_by_time: dict,
    pollutant: str,
    model: BaseRegressionModel,
    model_features: list,
    scaler,
    horizon: DatetimeIndex,
    lags: int = FORECAST_STEPS,
    label: str = "",
) -> Series:
    """Multistep recursive forecast over `horizon` with an already trained model.

    Parameters
    ----------
    dataframe: The sensor's readings, indexed by naive UTC hour (see load_sensor_data)
    weather_by_time: The sensor's weather rows keyed by Unix time
    pollutant: The pollutant that is used as a forecasting target
    model: An already trained model implementing the scikit-learn interface
    model_features: The features the model was trained on, in order
    scaler: The scaler fitted on those features at training time
    horizon: The hours to forecast, naive UTC
    lags: How much target history to keep for the lag features
    label: Who this forecast is for, for the logs

    Returns
    -------
    forecast_values: Series of forecasts indexed by `horizon`
    """
    if len(dataframe.index) == 0:
        return Series(dtype="float64")

    target = dataframe[pollutant].tail(lags * 2 + 1)
    model_lags = lags_of(model_features)

    forecasted_values = []
    for date in horizon:
        # Predict the value AT `date` from the features at the hour BEFORE it, which is the
        # pairing the model is trained on: x(t) -> y(t+1).
        #
        # This loop used to append a placeholder row AT `date` first and compute the lag
        # features there, one step out of step with training, and it seeded that placeholder
        # with 0.0 on the first iteration - a value the series never takes - so the opening
        # forecast was made from a fabricated lag. With the pairing corrected, that hour is a
        # real forecast and is kept, so this returns one value per hour of the horizon.
        previous_hour = date - timedelta(hours=1)
        try:
            data = weather_by_time.get(epoch(previous_hour), {})
            features = DataFrame(data, index=[previous_hour])
            features = concat([dataframe, features])
            features = features[~features.index.duplicated(keep="last")]
            # The model's own lags, never a fresh selection; see generate_features.
            features = features.join(
                generate_features(target, lags, fixed_lags=model_lags), how="inner"
            )
            features = features[model_features]
            encode_categorical_data(features)
            # Transform with the TRAINING scaler, not a fresh one fitted on this frame.
            features = apply_scaler(features, scaler)
            features = features.tail(1)
            prediction = model.predict(features)[-1]
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
            logger.exception(f"Could not forecast {pollutant} for {label} at {date}")
            forecasted_values.append(nan)
        # Feed the prediction back in at `date`, so the next step's lags can see it.
        target = concat([target, Series(forecasted_values[-1], [date])])
        target = target.tail(lags * 2 + 1)

    return Series(forecasted_values, horizon)

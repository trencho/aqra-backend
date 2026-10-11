from datetime import UTC, datetime, timedelta
from gc import collect
from importlib.metadata import PackageNotFoundError, version
from json import dumps
from logging import getLogger
from math import inf
from pathlib import Path
from pickle import HIGHEST_PROTOCOL
from pickle import dump as pickle_dump

from pandas import DataFrame, Series, to_datetime
from sklearn.model_selection import RandomizedSearchCV

from definitions import (
    DATA_PROCESSED_PATH,
    PIPELINE_SCHEMA,
    POLLUTANTS,
    REGRESSION_MODELS,
    RESULTS_ERRORS_PATH,
    RESULTS_PREDICTIONS_PATH,
)
from models import make_model
from models.base_regression_model import BaseRegressionModel
from models.registry import ModelKey, load_run, new_run_dir, publish, published_at
from processing import (
    apply_scaler,
    backward_elimination,
    encode_categorical_data,
    fetch_summary_dataframe,
    fit_scaler,
    generate_features,
)
from settings import get_settings
from utils import BatchOutcome, track_time
from visualization import draw_errors, draw_predictions

from .process_results import error_metrics, save_errors, save_results

logger = getLogger(__name__)

# A published model older than this is retrained on the next hourly fan-out.
RETRAIN_AFTER = timedelta(days=91)
LIBRARIES = ("lightgbm", "numpy", "pandas", "scikit-learn", "xgboost")
# Estimator parameters that set a thread count, by library.
THREAD_PARAMETERS = ("n_jobs", "num_threads")


def split_dataframe(
    dataframe: DataFrame,
    target: str,
    selected_features: list = None,
    scaler=None,
) -> tuple[DataFrame, Series, object]:
    x = dataframe.drop(columns=POLLUTANTS, errors="ignore")
    # Take the target from the NEXT row, and drop the final row of both: it has no next value.
    # This used to shift X FORWARD instead, via a helper called `previous_value_overwrite`, which
    # paired features from t+1 with a target at t. Because `lag_1` at t+1 is by definition the
    # value at t, the target became a column of X: measured on a random walk through this very
    # pipeline, the leaked column correlated with y at r = 1.000000, against 0.996887 for the same
    # column honestly aligned. Every error in results/errors/ was near-zero by construction, the
    # best model was selected on that error, and the served forecast collapsed to persistence.
    y = dataframe[target].shift(-1).iloc[:-1]

    if selected_features is None:
        # Selection runs on scaled columns, so no feature wins on magnitude alone.
        scaled, _ = fit_scaler(x)
        selected_features = backward_elimination(scaled.iloc[:-1], y)

    # Fit the scaler on the SELECTED columns only. It used to be fitted on every candidate
    # before selection, so the persisted scaler expected 37 columns while the model took 13: the
    # loader's own check refused every model training produced, and the forecast loop could not
    # have applied it to the selected frame anyway. RobustScaler works per column, so the model
    # sees exactly the values it did before.
    #
    # Fit only when no scaler is handed in. The caller fits ONCE, on the training split, and
    # passes it back for the validation split and on to inference; fitting on the whole frame
    # before the 75/25 split put the validation rows' statistics into the scaler.
    x = x[selected_features]
    if scaler is None:
        x, scaler = fit_scaler(x)
    else:
        x = apply_scaler(x, scaler)

    return x.iloc[:-1], y, scaler


def save_pipeline(
    run_dir: Path,
    selected_features: list,
    scaler,
    model_name: str,
    metrics: dict | None = None,
    window: tuple | None = None,
) -> None:
    """Write the feature list, the fitted scaler, and the manifest that ties them to the model.

    Call it AFTER the model is saved: the manifest is written last, so a run directory without
    one is a run that did not finish, and the loader refuses it. The manifest records what is
    needed to trust the run later: the model class (the loader no longer guesses it from a glob),
    the library versions a pickle depends on, the training window and the held-out metrics.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "selected_features.json").write_text(dumps(selected_features))
    with open(run_dir / "scaler.pkl", "wb") as out_file:
        pickle_dump(scaler, out_file, HIGHEST_PROTOCOL)
    (run_dir / "pipeline.json").write_text(
        dumps(
            {
                "schema": PIPELINE_SCHEMA,
                "created": datetime.now(UTC).isoformat(),
                "model": {"class": model_name, "file": f"{model_name}.mdl"},
                "features": list(selected_features),
                "scaler": {
                    "file": "scaler.pkl",
                    "n_features_in": int(getattr(scaler, "n_features_in_", 0)),
                },
                "libraries": library_versions(),
                "training": (
                    {"start": str(window[0]), "end": str(window[1])} if window else None
                ),
                "metrics": metrics,
            }
        )
    )


def library_versions() -> dict:
    versions = {}
    for name in LIBRARIES:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = None
    return versions


def create_path(data_path: Path) -> None:
    data_path.mkdir(parents=True, exist_ok=True)


def create_results_paths(data_path: Path) -> None:
    create_path(RESULTS_ERRORS_PATH / "data" / data_path)
    create_path(RESULTS_PREDICTIONS_PATH / "data" / data_path)


def hyper_parameter_tuning(
    model: BaseRegressionModel, x_train: DataFrame, y_train: Series
) -> dict:
    # n_jobs=1 for the search: the estimator gets the threads (see setup_model). Both used to
    # take cpu_count() // 2, so a search could run up to (cpu/2)^2 threads on a shared node.
    model_cv = RandomizedSearchCV(
        estimator=model.reg,
        param_distributions=model.param_grid,
        n_iter=50,
        n_jobs=1,
        cv=5,
        random_state=42,
    )
    model_cv.fit(x_train, y_train)
    return model_cv.best_params_


def check_best_regression_model(key: ModelKey) -> bool:
    """True when a published model exists and is younger than RETRAIN_AFTER."""
    if (created := published_at(key)) is None:
        return False
    return datetime.now(UTC) - created < RETRAIN_AFTER


def generate_regression_model(
    dataframe: DataFrame, city_name: str, sensor_id: str, pollutant: str
) -> bool:
    """Train every enabled model, publish the best as a new run. False if nothing published."""
    dataframe = dataframe.join(generate_features(dataframe[pollutant]), how="inner")
    dataframe = dataframe.dropna(axis="columns", how="all").dropna(
        axis="index", how="any"
    )
    encode_categorical_data(dataframe)
    validation_split = len(dataframe.index) * 3 // 4

    # Split BEFORE fitting the scaler. This used to scale the whole frame and slice afterwards,
    # which put the validation quarter's statistics into the scaler the model was fitted under.
    train_frame = dataframe.iloc[:validation_split]
    test_frame = dataframe.iloc[validation_split:]

    x_train, y_train, scaler = split_dataframe(train_frame, pollutant)
    selected_features = x_train.columns.values.tolist()
    x_test, y_test, _ = split_dataframe(
        test_frame, pollutant, selected_features, scaler=scaler
    )

    best_model_error = inf
    best_model = None
    best_metrics = None
    for model_name in REGRESSION_MODELS:
        model_data_path = Path(city_name) / sensor_id / pollutant / model_name
        try:
            model = setup_model(model_name, x_train, y_train)
        except Exception:
            logger.exception(
                f"Error occurred while training regression model for {city_name} - {sensor_id} - {pollutant} - "
                f"{model_name}",
            )
            continue

        y_predicted = model.predict(x_test)

        results = DataFrame({"Actual": y_test, "Predicted": y_predicted}, x_test.index)
        create_results_paths(model_data_path)
        save_results(model_data_path, results)

        if (
            model_error := save_errors(model_data_path, y_test, y_predicted)
        ) < best_model_error:
            best_model = model
            best_model_error = model_error
            best_metrics = error_metrics(y_test, y_predicted)

    if best_model is None:
        return False

    # The winner is already fitted on x_train; it used to go through a fourth full
    # hyperparameter search only to be saved at another path.
    key = ModelKey(city_name, sensor_id, pollutant)
    run_dir = new_run_dir(key)
    best_model.save(run_dir)
    save_pipeline(
        run_dir,
        selected_features,
        scaler,
        type(best_model).__name__,
        metrics=best_metrics,
        window=(train_frame.index[0], test_frame.index[-1]),
    )
    return publish(key, run_dir, verify=load_run)


def setup_model(
    model_name: str, x_train: DataFrame, y_train: Series
) -> BaseRegressionModel:
    model = make_model(model_name)
    threads = get_settings().training_jobs
    for parameter in THREAD_PARAMETERS:
        if parameter in model.param_grid:
            model.param_grid[parameter] = [threads]
    params = hyper_parameter_tuning(model, x_train, y_train)
    model.set_params(**params)
    model.train(x_train, y_train)
    return model


@track_time
def train_regression_model(city: dict, sensor: dict, pollutant: str) -> BatchOutcome:
    logger.info(
        f"Training model for {city['cityName']} - {sensor['sensorId']} - {pollutant}"
    )
    key = ModelKey(city["cityName"], sensor["sensorId"], pollutant)
    # No lock file any more: the worker enqueues each model under a unique RQ job id, so two
    # copies of this call for the same model cannot run at once.
    if check_best_regression_model(key):
        logger.info(
            f"Skipped training model for {city['cityName']} - {sensor['sensorId']} - {pollutant}"
        )
        return BatchOutcome.SKIPPED
    try:
        dataframe = fetch_summary_dataframe(
            DATA_PROCESSED_PATH / city["cityName"] / sensor["sensorId"],
            index_col="time",
        )
        dataframe = dataframe.loc[
            dataframe.index <= to_datetime(datetime.now(UTC)).to_datetime64()
        ]
        # A sensor that never reported this pollutant has nothing to train on. That is
        # not a completed training run, and it used to log as one -- the "Completed"
        # message below fired either way.
        if pollutant not in dataframe.columns:
            logger.info(
                f"No {pollutant} readings for {city['cityName']} - {sensor['sensorId']}; "
                f"nothing to train"
            )
            return BatchOutcome.SKIPPED

        published = generate_regression_model(
            dataframe, city["cityName"], sensor["sensorId"], pollutant
        )
        if published is False:
            logger.error(
                f"No model was published for {city['cityName']} - {sensor['sensorId']} - "
                f"{pollutant}"
            )
            return BatchOutcome.FAILED
        del dataframe
        draw_plots(city, sensor, pollutant)
        logger.info(
            f"Completed training model for {city['cityName']} - {sensor['sensorId']} - {pollutant}"
        )
        return BatchOutcome.DONE
    except Exception:
        logger.exception(
            f"Error occurred while training model for {city['cityName']} - "
            f"{sensor['sensorId']} - {pollutant}",
        )
        return BatchOutcome.FAILED
    finally:
        collect()


def draw_plots(city: dict, sensor: dict, pollutant: str) -> None:
    """Best-effort. A plot is a report on a run, and failing to draw one is not a failed run.

    The plotters read error.csv for every enabled model, so one model that failed to train
    used to raise here and report a run as FAILED after its model had already been saved.
    """
    try:
        draw_errors(city, sensor, pollutant)
        draw_predictions(city, sensor, pollutant)
    except Exception:
        logger.exception(
            f"Could not draw plots for {city['cityName']} - {sensor['sensorId']} - {pollutant}"
        )

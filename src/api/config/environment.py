from gc import collect
from logging import getLogger

from pandas import DataFrame

from definitions import (
    COLLECTIONS,
    DATA_EXTERNAL_PATH,
    DATA_PROCESSED_PATH,
    DATA_RAW_PATH,
    LOG_PATH,
    MODELS_PATH,
    RESULTS_ERRORS_PATH,
    RESULTS_PREDICTIONS_PATH,
)
from preparation import read_cities, read_sensors
from processing import find_missing_data, read_csv_in_chunks, save_dataframe
from repository import get_repository
from settings import missing_required_variables
from utils import BatchOutcome, BatchTally

from .schedule import fetch_locations

logger = getLogger(__name__)


SYSTEM_PATHS = [
    DATA_EXTERNAL_PATH,
    DATA_PROCESSED_PATH,
    DATA_RAW_PATH,
    LOG_PATH,
    MODELS_PATH,
    RESULTS_ERRORS_PATH,
    RESULTS_PREDICTIONS_PATH,
]


def init_environment_variables() -> None:
    if missing_envs := missing_required_variables():
        logger.error(f"Missing environment variables: {', '.join(missing_envs)}")
        raise SystemExit(1)


# TODO: Review this method for inserting duplicate values
def fetch_collection(collection: str, city_name: str, sensor_id: str) -> BatchOutcome:
    db_records = DataFrame(
        get_repository().get_many(
            collection_name=collection,
            filter={"sensorId": sensor_id},
            projection={"_id": False, "sensorId": False},
        )
    )
    if db_records.empty:
        return BatchOutcome.SKIPPED

    collection_dir = DATA_RAW_PATH / city_name / sensor_id
    collection_dir.mkdir(parents=True, exist_ok=True)
    collection_path = collection_dir / f"{collection}.csv"
    try:
        dataframe = read_csv_in_chunks(collection_path)
        new_db_records = find_missing_data(db_records, dataframe, "time")
        # The column-dtype conversion is deliberately unfinished, and this is its one record.
        #
        # `processing.handle_data` already carries the pieces for it -- `store_dtypes` writes
        # `{collection}_dtypes.json`, `find_dtypes` reads it back, `convert_dtype` renders a cell --
        # and all three are covered by tests but called from nowhere, because the conversion was
        # never wired up here. `convert_dtype`'s docstring points at this function by name.
        #
        # It used to be recorded as five byte-identical `# TODO: Review this line for converting
        # column data types` comments beside five commented-out `astype(column_dtypes, ...)` calls,
        # which read as five separate chores rather than the one unresolved question it is: should
        # these frames be dtype-cast on write, and should the merge de-duplicate on `time`? The
        # other four are gone; this is the one that stays, next to the code it would change.
        new_db_records.to_csv(collection_path, header=False, index=False, mode="a")

        save_dataframe(dataframe, collection, collection_path, sensor_id)
        del dataframe
        return BatchOutcome.DONE
    except Exception:
        logger.exception(
            f"Could not fetch data from local storage for {city_name} - {sensor_id} - {collection}",
        )
        # The records are still written, straight from the database rather than merged
        # with local storage, so this is a degraded write and not a lost one -- but it is
        # reported as FAILED because the merge it was asked to do did not happen.
        db_records.to_csv(collection_path, index=False)
        return BatchOutcome.FAILED
    finally:
        del db_records
        collect()


def fetch_db_data() -> None:
    with BatchTally(logger, "fetch_db_data", "collection") as tally:
        for city in read_cities():
            for sensor in read_sensors(city["cityName"]):
                for collection in tally.track(COLLECTIONS):
                    try:
                        tally.record(
                            fetch_collection(
                                collection, city["cityName"], sensor["sensorId"]
                            )
                        )
                    except Exception:
                        tally.failure(
                            f"Could not fetch data from the database for {city['cityName']} - "
                            f"{sensor['sensorId']} - {collection}",
                        )


def init_data() -> None:
    fetch_locations()
    fetch_db_data()


def init_system_paths() -> None:
    for system_path in SYSTEM_PATHS:
        system_path.mkdir(parents=True, exist_ok=True)

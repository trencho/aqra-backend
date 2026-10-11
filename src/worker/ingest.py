"""Ingest jobs: the location catalogue, hourly readings, imports, and the startup rebuild."""

from gc import collect
from json import dumps
from logging import getLogger
from os import walk
from pathlib import Path
from shutil import unpack_archive

from pandas import DataFrame

from caching import cache
from definitions import (
    COLLECTIONS,
    DATA_EXTERNAL_PATH,
    DATA_PATH,
    DATA_RAW_PATH,
    OPEN_WEATHER,
)
from preparation import (
    api_is_available,
    fetch_cities,
    fetch_city_data,
    fetch_countries,
    fetch_sensors,
    read_cities,
    read_sensors,
)
from processing import (
    find_missing_data,
    process_data,
    read_csv_in_chunks,
    save_dataframe,
)
from repository import get_repository
from settings import get_settings
from utils import BatchOutcome, BatchTally, track_time

from .context import in_app_context

logger = getLogger(__name__)


@in_app_context
@track_time
def bootstrap() -> None:
    """Refresh the location catalogue, then rebuild the local CSVs from the database.

    This ran inside the API's create_app() on every boot, which is why the flask pod needed a
    fifteen-minute startup probe: gunicorn could not bind until it finished. The worker runs it
    once when it starts instead, and the API serves whatever is on the volume meanwhile.

    SKIP_DATA_FETCH skips it, so a worker can start offline.
    """
    if get_settings().skip_data_fetch:
        logger.info("bootstrap: SKIP_DATA_FETCH is set; nothing fetched")
        return
    fetch_locations()
    fetch_db_data()


@in_app_context
@track_time
def fetch_hourly_data() -> None:
    if not api_is_available():
        logger.warning(
            "fetch_hourly_data: the OpenWeather API is locked; nothing fetched"
        )
        return
    # Two tallies, because the two halves fail for unrelated reasons and a single number
    # would hide which one. An upstream outage empties the first; a processing defect
    # empties the second while the fetch keeps working.
    with (
        BatchTally(logger, "fetch_hourly_data (fetch)", "sensor") as fetched,
        BatchTally(logger, "fetch_hourly_data (process)", "collection") as processed,
    ):
        for city in cache.get("cities") or read_cities():
            for sensor in fetched.track(read_sensors(city["cityName"])):
                fetched.record(fetch_city_data(city["cityName"], sensor))
                for collection in COLLECTIONS:
                    if (
                        DATA_RAW_PATH
                        / city["cityName"]
                        / sensor["sensorId"]
                        / f"{collection}.csv"
                    ).exists():
                        processed.attempt()
                        processed.record(
                            process_data(
                                city["cityName"], sensor["sensorId"], collection
                            )
                        )


@in_app_context
@track_time
def fetch_locations() -> None:
    countries = fetch_countries()
    if countries:
        (DATA_RAW_PATH / "countries.json").write_text(dumps(countries, indent=4))
        cache.set("countries", countries)
    else:
        logger.warning(
            "Upstream returned no countries; keeping the stored list rather than emptying it",
        )
    with BatchTally(logger, "fetch_locations (countries)", "country") as tally:
        for country in tally.track(countries):
            try:
                get_repository().save(
                    collection_name="countries",
                    filter={"countryCode": country["countryCode"]},
                    item=country,
                )
            except Exception:
                tally.failure(
                    f"Error occurred while updating data for {country['countryName']}",
                )

    # fetch_countries / fetch_cities / fetch_sensors each answer an upstream failure with an
    # empty list, so writing the result straight to disk turns a transient pulse.eco outage
    # into a wiped location catalogue: read_cities and read_sensors serve these files, and
    # everything downstream -- the scheduled data pull, the forecast endpoints -- then has
    # nothing to iterate until the next successful run. Refreshing with what we got and
    # keeping the last good copy otherwise is the safe direction.
    cities = fetch_cities()
    if cities:
        (DATA_RAW_PATH / "cities.json").write_text(dumps(cities, indent=4))
        cache.set("cities", cities)
    else:
        logger.warning(
            "Upstream returned no cities; keeping the stored list rather than emptying it",
        )
    with (
        BatchTally(logger, "fetch_locations (cities)", "city", "cities") as city_tally,
        BatchTally(logger, "fetch_locations (sensors)", "sensor") as sensor_tally,
    ):
        for city in city_tally.track(cities):
            try:
                get_repository().save(
                    collection_name="cities",
                    filter={"cityName": city["cityName"]},
                    item=city,
                )
            except Exception:
                city_tally.failure(
                    f"Error occurred while updating data for {city['cityName']}",
                )
            sensors = fetch_sensors(city["cityName"])
            for sensor in sensor_tally.track(sensors):
                sensor["cityName"] = city["cityName"]
                try:
                    get_repository().save(
                        collection_name="sensors",
                        filter={"sensorId": sensor["sensorId"]},
                        item=sensor,
                    )
                except Exception:
                    sensor_tally.failure(
                        f"Error occurred while updating data for {sensor['sensorId']}",
                    )
            if sensors:
                (DATA_RAW_PATH / city["cityName"]).mkdir(parents=True, exist_ok=True)
                (DATA_RAW_PATH / city["cityName"] / "sensors.json").write_text(
                    dumps(sensors, indent=4)
                )
            else:
                logger.warning(
                    f"Upstream returned no sensors for {city['cityName']}; keeping the stored list",
                )


@in_app_context
def import_data() -> None:
    for root, _, files in walk(DATA_EXTERNAL_PATH):
        for file in files:
            file_path = Path(root) / file
            fmt = file_path.suffix.lstrip(".")
            if fmt == "zip":
                unpack_archive(filename=str(file_path), extract_dir=root, format=fmt)
                file_path.unlink(missing_ok=True)

    with BatchTally(logger, "import_data", "file") as tally:
        for root, directories, files in walk(DATA_EXTERNAL_PATH):
            for file in files:
                if file.endswith(".csv"):
                    file_path = Path(root) / file
                    tally.attempt()
                    try:
                        dataframe = read_csv_in_chunks(file_path)
                        save_dataframe(
                            dataframe,
                            Path(file).stem,
                            DATA_RAW_PATH / file_path.relative_to(DATA_EXTERNAL_PATH),
                            file_path.parent.name,
                        )
                        file_path.unlink(missing_ok=True)
                    except Exception:
                        tally.failure(
                            f"Error occurred while importing data from {file_path}",
                        )
            if not directories and not files:
                Path(root).rmdir()

    DATA_EXTERNAL_PATH.mkdir(parents=True, exist_ok=True)


def reset_api_counter() -> None:
    (DATA_PATH / f"{OPEN_WEATHER}.lock").unlink(missing_ok=True)


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

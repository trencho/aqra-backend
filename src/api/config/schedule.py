from atexit import register
from base64 import b64encode
from datetime import datetime
from json import dumps, loads
from logging import getLogger
from os import walk
from pathlib import Path
from shutil import unpack_archive

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import create_engine

from definitions import (
    COLLECTIONS,
    DATA_EXTERNAL_PATH,
    DATA_PATH,
    DATA_PROCESSED_PATH,
    DATA_RAW_PATH,
    MODELS_PATH,
    OPEN_WEATHER,
    POLLUTANTS,
)
from modeling import train_regression_model
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
    current_hour,
    fetch_forecast_result,
    process_data,
    read_csv_in_chunks,
    save_dataframe,
)
from repository import get_repository
from settings import get_settings
from utils import BatchTally, track_time

from .cache import cache
from .dump import generate_sql_dump
from .git import append_commit_files, create_archive, update_git_files

logger = getLogger(__name__)

scheduler = BackgroundScheduler()
jobstore_name = "aqra"
DATABASE_FILE = DATA_PATH / "jobs.sqlite"


@scheduler.scheduled_job(
    trigger="cron",
    id="dump_data",
    misfire_grace_time=None,
    jobstore=jobstore_name,
    day="*/15",
)
@track_time
def dump_data() -> None:
    file_list, file_names = [], []
    for root, directories, files in walk(DATA_PATH):
        if not directories and files:
            file_path = Path(f"{root}.zip")
            create_archive(source=Path(root), destination=file_path)
            data = b64encode(file_path.read_bytes())
            append_commit_files(
                file_list,
                data,
                Path(root).resolve().parent,
                Path(file_path).name,
                file_names,
            )
            file_path.unlink(missing_ok=True)

    if file_list:
        update_git_files(
            file_list,
            file_names,
            get_settings().repo_name,
            "master",
            f"Scheduled data dump - {datetime.now().strftime('%H:%M:%S %d-%m-%Y')}",
        )


@scheduler.scheduled_job(
    trigger="cron",
    id="dump_jobs",
    misfire_grace_time=None,
    jobstore=jobstore_name,
    hour=0,
)
@track_time
def dump_jobs() -> None:
    dump_filename = "job_dump.sql"
    dump_content = generate_sql_dump(DATABASE_FILE)

    dump_path = DATA_PATH / dump_filename
    dump_path.write_text(dump_content)


@scheduler.scheduled_job(
    trigger="cron",
    id="fetch_hourly_data",
    misfire_grace_time=None,
    jobstore=jobstore_name,
    hour="*/2",
)
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


@scheduler.scheduled_job(
    trigger="cron",
    id="fetch_locations",
    misfire_grace_time=None,
    jobstore=jobstore_name,
    hour=0,
)
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


@scheduler.scheduled_job(
    trigger="cron",
    id="import_data",
    misfire_grace_time=None,
    jobstore=jobstore_name,
    hour=0,
)
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


@scheduler.scheduled_job(
    trigger="cron",
    id="model_training",
    misfire_grace_time=None,
    jobstore=jobstore_name,
    minute=0,
)
@track_time
def model_training() -> None:
    with BatchTally(logger, "model_training", "model") as tally:
        for city in cache.get("cities") or read_cities():
            for sensor in read_sensors(city["cityName"]):
                for pollutant in tally.track(POLLUTANTS):
                    tally.record(train_regression_model(city, sensor, pollutant))


@scheduler.scheduled_job(
    trigger="cron",
    id="predict_locations",
    misfire_grace_time=None,
    jobstore=jobstore_name,
    minute=0,
)
@track_time
def predict_locations() -> None:
    with BatchTally(logger, "predict_locations (publish)", "sensor") as tally:
        for city in cache.get("cities") or read_cities():
            for sensor in tally.track(read_sensors(city["cityName"])):
                file_path = (
                    DATA_PROCESSED_PATH
                    / city["cityName"]
                    / sensor["sensorId"]
                    / "predictions.json"
                )
                try:
                    get_repository().save(
                        collection_name="predictions",
                        filter={
                            "cityName": city["cityName"],
                            "sensorId": sensor["sensorId"],
                        },
                        item={
                            "data": loads(file_path.read_text()),
                            "cityName": city["cityName"],
                            "sensorId": sensor["sensorId"],
                        },
                    )
                except Exception:
                    tally.failure(
                        f"Error occurred while updating forecast values from {file_path}",
                    )

    with BatchTally(logger, "predict_locations (forecast)", "sensor") as tally:
        for city in cache.get("cities") or read_cities():
            for sensor in tally.track(read_sensors(city["cityName"])):
                try:
                    forecast_result = fetch_forecast_result(city, sensor)
                    (
                        DATA_PROCESSED_PATH
                        / city["cityName"]
                        / sensor["sensorId"]
                        / "predictions.json"
                    ).write_text(
                        dumps(list(forecast_result.values()), indent=4, default=str)
                    )
                except Exception:
                    tally.failure(
                        f"Error occurred while fetching forecast values for "
                        f"{city['cityName']} - {sensor['sensorId']}",
                    )


@scheduler.scheduled_job(
    trigger="cron",
    id="reset_api_counter",
    misfire_grace_time=None,
    jobstore=jobstore_name,
    hour=0,
)
def reset_api_counter() -> None:
    (DATA_PATH / f"{OPEN_WEATHER}.lock").unlink(missing_ok=True)


@scheduler.scheduled_job(
    trigger="cron",
    id="reset_model_lock",
    misfire_grace_time=None,
    jobstore=jobstore_name,
    month="*/2",
)
def reset_model_lock() -> None:
    for file in [
        Path(root) / file
        for root, _, files in walk(MODELS_PATH)
        for file in files
        if file.endswith(".lock")
    ]:
        last_modified = int(file.stat().st_mtime)
        if last_modified < int(datetime.timestamp(current_hour())) - 3600:
            (MODELS_PATH / file).unlink(missing_ok=True)


def init_scheduler() -> None:
    db_url = f"sqlite:///{DATABASE_FILE}"
    engine = create_engine(db_url)
    jobstore = SQLAlchemyJobStore(engine=engine)
    scheduler.add_jobstore(jobstore=jobstore, alias=jobstore_name)
    scheduler.start()
    register(lambda: scheduler.shutdown(wait=False))

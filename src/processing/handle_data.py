from logging import getLogger
from os import replace
from pathlib import Path
from typing import Optional
from uuid import uuid4

from pandas import DataFrame, concat, read_csv, to_datetime

from definitions import CHUNK_SIZE, COLLECTIONS, READING_KEYS
from repository import get_repository

logger = getLogger(__name__)


def drop_unnecessary_features(dataframe: DataFrame) -> DataFrame:
    dataframe = dataframe.drop(
        columns=dataframe.filter(regex="weather").columns, errors="ignore"
    )
    dataframe = dataframe.drop(
        columns=["precipProbability", "precipType", "ozone", "co2"], errors="ignore"
    )
    return dataframe


def find_missing_data(
    new_dataframe: DataFrame, old_dataframe: DataFrame, column: str
) -> DataFrame:
    dataframe = new_dataframe.loc[
        ~new_dataframe[column].isin(old_dataframe[column])
    ].copy()
    return dataframe[
        dataframe.columns.intersection(old_dataframe.columns.values.tolist())
    ]


def read_csv_in_chunks(data_path: Path, index_col: str = None) -> Optional[DataFrame]:
    chunks = [
        chunk
        for chunk in read_csv(data_path, index_col=index_col, chunksize=CHUNK_SIZE)
        if len(chunk.index) > 0
    ]
    dataframe = concat(chunks)
    dataframe.index = to_datetime(dataframe.index, errors="coerce", unit="s")
    dataframe = dataframe.drop_duplicates(keep="last")
    return dataframe.sort_index() if len(dataframe.index) > 0 else None


def rename_features(dataframe: DataFrame) -> DataFrame:
    return dataframe.rename(
        columns={
            "dt": "time",
            "temperature": "temp",
            "apparentTemperature": "feels_like",
            "dewPoint": "dew_point",
            "cloudCover": "clouds",
            "windSpeed": "wind_speed",
            "windGust": "wind_gust",
            "windBearing": "wind_deg",
            "summary": "weather.description",
            "icon": "weather.icon",
            "uvIndex": "uvi",
            "precipIntensity": "precipitation",
            "AQI": "aqi",
            "CO": "co",
            "CO2": "co2",
            "NH3": "nh3",
            "NO": "no",
            "NO2": "no2",
            "O3": "o3",
            "PM25": "pm2_5",
            "PM10": "pm10",
            "SO2": "so2",
        },
        errors="ignore",
    )


def fetch_summary_dataframe(data_path: Path, index_col: str) -> DataFrame:
    dataframe_list = [
        read_csv_in_chunks(data_path / f"{collection}.csv", index_col=index_col)
        for collection in COLLECTIONS
    ]
    return concat(dataframe_list, axis=1, join="inner")


def save_dataframe(
    dataframe: DataFrame, collection: str, collection_path: Path, sensor_id: str
) -> None:
    """Store new readings in the database, then append them to the local CSV cache.

    The database is the source of truth and is written first. The insert skips any
    (sensorId, time) already stored, so nothing reads the sensor's collection back first.
    """
    dataframe = rename_features(dataframe)
    dataframe = drop_unnecessary_features(dataframe)
    dataframe = trim_dataframe(dataframe, "time")
    if len(dataframe.index) == 0:
        return

    get_repository().insert_missing(
        collection,
        dataframe.assign(sensorId=sensor_id).to_dict("records"),
        READING_KEYS,
    )

    try:
        df = read_csv_in_chunks(collection_path)
        dataframe = find_missing_data(dataframe, df, "time")
    except Exception:
        logger.exception(
            f"Could not fetch data from local storage for {sensor_id} - {collection}",
        )
    dataframe.to_csv(
        collection_path, header=not collection_path.exists(), index=False, mode="a"
    )


def write_csv_atomically(dataframe: DataFrame, path: Path) -> None:
    """Replace `path` in one rename, so a reader never sees a half-written cache file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{uuid4().hex}")
    dataframe.to_csv(staged, index=False)
    replace(staged, path)


def trim_dataframe(dataframe: DataFrame, column: str) -> DataFrame:
    dataframe = dataframe.sort_values(by=column)
    dataframe = dataframe.dropna(axis="columns", how="all")
    dataframe = dataframe.dropna(axis="index", how="all")
    dataframe = dataframe.drop_duplicates(subset=column, keep="last")
    dataframe = dataframe.reset_index(drop=True)
    return dataframe

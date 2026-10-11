from datetime import datetime, tzinfo
from logging import getLogger

from pandas import Series, concat, to_numeric
from pytz import timezone
from sklearn.impute import KNNImputer

from definitions import DATA_PROCESSED_PATH, DATA_RAW_PATH, POLLUTANTS
from utils import BatchOutcome

from .calculate_index import calculate_aqi, calculate_index
from .handle_data import (
    drop_unnecessary_features,
    find_missing_data,
    read_csv_in_chunks,
    rename_features,
    trim_dataframe,
)

logger = getLogger(__name__)


def calculate_row_index(row: Series) -> float:
    row = to_numeric(row, errors="coerce")

    return calculate_aqi(
        calculate_index("co", row["co"]),
        calculate_index("no2", row["no2"]),
        calculate_index("o3", row["o3"]),
        calculate_index("pm2_5", row["pm2_5"]),
        calculate_index("pm10", row["pm10"]),
        calculate_index("so2", row["so2"]),
    )


def current_hour(tz: tzinfo = None) -> datetime:
    current_datetime = datetime.now()
    if tz is not None:
        return timezone(tz.__str__()).localize(
            current_datetime.replace(minute=0, second=0, microsecond=0)
        )

    return current_datetime.replace(minute=0, second=0, microsecond=0)


def flatten_json(nested_json: dict, exclude=None) -> dict:
    """
    Flatten a list of nested dicts.
    """
    if exclude is None:
        exclude = [""]
    out = {}

    def flatten(x: (list, dict, str), name: str = "", exclude=exclude) -> None:
        if isinstance(x, dict):
            for a in x:
                if a not in exclude:
                    flatten(x[a], f"{name}{a}_")
        elif isinstance(x, list):
            if len(x) == 1:
                flatten(x[0], f"{name}")
            else:
                i = 0
                for a in x:
                    flatten(a, f"{name}{i}_")
                    i += 1
        else:
            out[name[:-1]] = x

    flatten(nested_json)
    return out


# TODO: Refactor this method to reduce number of exceptions based on missing dataset
def process_data(city_name: str, sensor_id: str, collection: str) -> BatchOutcome:
    try:
        dataframe_raw = read_csv_in_chunks(
            DATA_RAW_PATH / city_name / sensor_id / f"{collection}.csv"
        )

        collection_path = (
            DATA_PROCESSED_PATH / city_name / sensor_id / f"{collection}.csv"
        )
        dataframe_processed = None
        if collection_path.exists():
            dataframe_processed = read_csv_in_chunks(collection_path)
            dataframe_raw = find_missing_data(
                dataframe_raw, dataframe_processed, "time"
            )
            dataframe_raw = concat(
                df.dropna(axis=1, how="all")
                for df in [dataframe_processed, dataframe_raw]
            )

        dataframe_raw = rename_features(dataframe_raw)
        dataframe_raw = drop_unnecessary_features(dataframe_raw)
        dataframe_raw = trim_dataframe(dataframe_raw, "time")
        if len(dataframe_raw.index) == 0:
            return BatchOutcome.SKIPPED

        df_columns = dataframe_raw.columns.copy()
        df_columns = df_columns.drop(
            ["aqi", "icon", "precipType", "summary"], errors="ignore"
        )

        imp = KNNImputer()
        dataframe_raw[df_columns] = dataframe_raw[df_columns].apply(
            to_numeric, axis="columns", errors="coerce"
        )
        for column in df_columns:
            if dataframe_raw[column].isna().all():
                dataframe_raw = dataframe_raw.drop(columns=column, errors="ignore")
            if dataframe_raw[column].isna().any():
                dataframe_raw[column] = imp.fit_transform(
                    dataframe_raw[column].values.reshape(-1, 1)
                )

        pollutants_wo_aqi = POLLUTANTS.copy()
        pollutants_wo_aqi.pop("aqi")
        columns = pollutants_wo_aqi.copy()
        for column in columns:
            if column not in dataframe_raw.columns:
                pollutants_wo_aqi.pop(column)

        drop_columns_std = (
            dataframe_raw[list(pollutants_wo_aqi)]
            .std()[dataframe_raw[list(pollutants_wo_aqi)].std() == 0]
            .index.values
        )
        dataframe_raw = dataframe_raw.drop(columns=drop_columns_std, errors="ignore")

        if collection != "weather":
            dataframe_raw["aqi"] = dataframe_raw[list(POLLUTANTS)].apply(
                calculate_row_index, axis=1
            )

        if dataframe_processed is not None:
            dataframe_raw = find_missing_data(
                dataframe_raw, dataframe_processed, "time"
            )

        if len(dataframe_raw.index) > 0:
            dataframe_raw.to_csv(
                collection_path,
                header=not collection_path.exists(),
                index=False,
                mode="a",
            )
            return BatchOutcome.DONE

        # Everything new was already present downstream, so there was nothing to write.
        # Reported apart from DONE so a run that processed no new readings at all is
        # visible as such rather than as a run that processed everything.
        return BatchOutcome.SKIPPED

    except Exception:
        logger.exception(
            f"Error occurred while processing {collection} data for {city_name} - {sensor_id}",
        )
        return BatchOutcome.FAILED

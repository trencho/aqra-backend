"""Hermetic unit tests for the pure DataFrame transforms in ``processing/handle_data``.

Covers the small, I/O-free helpers (the CSV/repository functions around them are not
exercised here). ``test_drop_unnecessary_features`` is also a regression test: the drop
passed both ``columns=`` and ``axis=1``, which pandas 3 rejects, so the function raised
``ValueError`` on every call until the redundant ``axis`` was removed.
"""

from numpy import nan
from pandas import DataFrame

# Import the config package first so the api/preparation/processing chain initialises
# in order — importing a ``processing`` submodule first hits a circular import.
import api.config  # noqa: F401
from processing.handle_data import (
    drop_unnecessary_features,
    find_missing_data,
    rename_features,
    trim_dataframe,
)


def test_drop_unnecessary_features():
    dataframe = DataFrame(
        {
            "time": [1],
            "weather.icon": ["x"],  # matches the "weather" regex → dropped
            "precipType": ["rain"],  # explicitly dropped
            "co2": [1],  # explicitly dropped
            "temp": [5],  # kept
            "pm2_5": [9],  # kept
        }
    )
    result = drop_unnecessary_features(dataframe)
    assert list(result.columns) == ["time", "temp", "pm2_5"]


def test_rename_features_maps_known_and_leaves_unknown():
    dataframe = DataFrame(
        {"dt": [1], "temperature": [5], "CO": [0.4], "PM25": [9], "unknown": [1]}
    )
    result = rename_features(dataframe)
    assert list(result.columns) == ["time", "temp", "co", "pm2_5", "unknown"]


def test_find_missing_data_returns_new_rows_intersected_to_old_columns():
    new = DataFrame({"time": [1, 2, 3], "v": [10, 20, 30], "w": [7, 8, 9]})
    old = DataFrame({"time": [1, 2], "v": [10, 20]})
    result = find_missing_data(new, old, "time")
    # Only time=3 is absent from old; the extra "w" column (not in old) is dropped.
    assert list(result.columns) == ["time", "v"]
    assert result.to_dict("records") == [{"time": 3, "v": 30}]


def test_trim_dataframe_sorts_dedupes_last_and_drops_empty_columns():
    dataframe = DataFrame(
        {
            "time": [3, 1, 2, 2],
            "v": [30, 10, 20, 25],
            "empty": [nan, nan, nan, nan],  # all-NA column → dropped
        }
    )
    result = trim_dataframe(dataframe, "time")
    assert list(result.columns) == ["time", "v"]
    # Sorted by time; the duplicate time=2 keeps the last row (v=25).
    assert result.to_dict("records") == [
        {"time": 1, "v": 10},
        {"time": 2, "v": 25},
        {"time": 3, "v": 30},
    ]

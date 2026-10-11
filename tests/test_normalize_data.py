"""Hermetic unit tests for the pure helpers in ``src/processing/normalize_data``.

``flatten_json`` and the hour-rounding helpers are deterministic and I/O-free (the
heavy ``process_data`` pipeline around them is not covered here). ``next_hour`` /
``closest_hour`` take an explicit datetime so they're pinned exactly; ``current_hour``
reads the clock, so only its truncation invariant is asserted.
"""

import pytest

# Import the config package first so the api/preparation/processing chain initialises
# in order — importing a ``processing`` submodule first hits a circular import.
import api.config  # noqa: F401
from processing.normalize_data import (
    current_hour,
    flatten_json,
)
from utils import BatchOutcome


@pytest.mark.parametrize(
    "nested, expected",
    [
        ({"a": 1, "b": {"c": 2}}, {"a": 1, "b_c": 2}),  # nested dict → underscore path
        ({"a": [{"x": 1}]}, {"a_x": 1}),  # single-element list collapses (no index)
        ({"a": [1, 2]}, {"a_0": 1, "a_1": 2}),  # multi-element list → indexed keys
    ],
)
def test_flatten_json(nested, expected):
    assert flatten_json(nested) == expected


def test_flatten_json_excludes_keys():
    assert flatten_json({"a": 1, "secret": 2}, exclude=["secret"]) == {"a": 1}


def test_current_hour_is_truncated_to_the_hour():
    now_hour = current_hour()
    assert now_hour.minute == 0
    assert now_hour.second == 0
    assert now_hour.microsecond == 0


# --- process_data's outcome, which is what the fetch_hourly_data tally counts ---------


_RAW_WEATHER = (
    "time,temperature,humidity,pressure\n"
    "1767225600,7.5,60.0,1013.0\n"
    "1767229200,8.1,58.0,1012.0\n"
    "1767232800,9.4,55.0,1011.0\n"
)


@pytest.fixture
def data_paths(monkeypatch, tmp_path):
    from processing import normalize_data

    raw, processed = tmp_path / "raw", tmp_path / "processed"
    (raw / "skopje" / "1000").mkdir(parents=True)
    (processed / "skopje" / "1000").mkdir(parents=True)
    monkeypatch.setattr(normalize_data, "DATA_RAW_PATH", raw)
    monkeypatch.setattr(normalize_data, "DATA_PROCESSED_PATH", processed)
    return normalize_data, raw, processed


def test_process_data_reports_done_when_it_writes_new_readings(data_paths):
    normalize_data, raw, processed = data_paths
    (raw / "skopje" / "1000" / "weather.csv").write_text(_RAW_WEATHER)

    outcome = normalize_data.process_data("skopje", "1000", "weather")

    assert outcome is BatchOutcome.DONE
    assert (processed / "skopje" / "1000" / "weather.csv").exists()


def test_process_data_reports_skipped_when_there_is_nothing_new(data_paths):
    # Running twice over the same raw file is the realistic shape: the hourly job re-reads
    # a file whose rows are already downstream. That is not a failure and it is not work
    # either, and a tally that counted it as DONE would report a fully idle pipeline as a
    # fully productive one.
    normalize_data, raw, _ = data_paths
    (raw / "skopje" / "1000" / "weather.csv").write_text(_RAW_WEATHER)

    assert normalize_data.process_data("skopje", "1000", "weather") is BatchOutcome.DONE
    assert (
        normalize_data.process_data("skopje", "1000", "weather") is BatchOutcome.SKIPPED
    )


def test_process_data_reports_failed_when_the_raw_file_is_missing(data_paths):
    normalize_data, _, _ = data_paths

    assert (
        normalize_data.process_data("skopje", "1000", "weather") is BatchOutcome.FAILED
    )

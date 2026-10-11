"""MongoDB as the source of truth, the CSVs as a rebuildable cache, and the migration.

Hermetic: a fresh InMemoryRepository per test stands in for Mongo (mongomock cannot run
pymongo 4.18's bulk_write), and the RegularRepository tests stub the collection to check the
exact operations sent.
"""

import pytest
from pandas import DataFrame, read_csv
from pymongo.errors import OperationFailure

import repository as repository_module
from definitions import READING_KEYS
from processing import handle_data
from repository import InMemoryRepository, RegularRepository, RepositorySingleton
from worker import ingest, migrate

ROWS = [{"time": 3600 * hour, "pm10": float(hour)} for hour in range(1, 4)]


@pytest.fixture
def store(monkeypatch):
    fresh = InMemoryRepository()
    # Patch both: an instance created during the test would outlive the restored
    # `_repository = None`, and every later get_repository() would return None.
    monkeypatch.setattr(RepositorySingleton, "_instance", None)
    monkeypatch.setattr(RepositorySingleton, "_repository", fresh)
    return fresh


@pytest.fixture
def raw(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "DATA_RAW_PATH", tmp_path)
    return tmp_path


def _csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    DataFrame(rows).to_csv(path, index=False)


def test_insert_missing_never_overwrites_and_counts_only_new_rows(store):
    readings = [dict(row, sensorId="1000") for row in ROWS]
    assert store.insert_missing("pollution", readings, READING_KEYS) == 3
    revised = [dict(ROWS[0], sensorId="1000", pm10=99.0)]
    assert store.insert_missing("pollution", revised, READING_KEYS) == 0
    assert store.count("pollution") == 3
    assert store.get("pollution", {"time": 3600})["pm10"] == 1.0


def test_save_dataframe_writes_the_database_then_the_cache_without_duplicates(
    store, tmp_path
):
    path = tmp_path / "pollution.csv"
    handle_data.save_dataframe(DataFrame(ROWS[:2]), "pollution", path, "1000")
    handle_data.save_dataframe(DataFrame(ROWS[1:]), "pollution", path, "1000")

    assert store.count("pollution", {"sensorId": "1000"}) == 3
    assert list(read_csv(path)["time"]) == [3600, 7200, 10800]


def test_rebuild_pushes_local_only_rows_before_rewriting_the_cache(store, raw):
    # The database is missing the last hour, which only the cache holds (an older deploy).
    store.insert_missing(
        "pollution", [dict(row, sensorId="1000") for row in ROWS[:2]], READING_KEYS
    )
    path = raw / "skopje" / "1000" / "pollution.csv"
    _csv(path, ROWS)

    assert ingest.rebuild_cache("pollution", "skopje", "1000").value == "done"

    assert store.count("pollution") == 3  # nothing lost
    assert list(read_csv(path)["time"]) == [3600, 7200, 10800]
    assert "sensorId" not in read_csv(path).columns


def test_rebuild_with_nothing_anywhere_skips_and_writes_nothing(store, raw):
    assert ingest.rebuild_cache("pollution", "skopje", "1000").value == "skipped"
    assert not (raw / "skopje" / "1000" / "pollution.csv").exists()


def test_migrate_twice_changes_nothing_the_second_time(store, raw, monkeypatch):
    monkeypatch.setattr(migrate, "read_cities", lambda: [{"cityName": "skopje"}])
    monkeypatch.setattr(migrate, "read_sensors", lambda city: [{"sensorId": "1000"}])
    _csv(raw / "skopje" / "1000" / "pollution.csv", ROWS)
    # A duplicate an older insert path could have written.
    store.save("pollution", None, dict(ROWS[0], sensorId="1000"))
    store.save("pollution", None, dict(ROWS[0], sensorId="1000"))

    assert migrate.migrate(dry_run=True)["pollution"] == 2
    assert store.count("pollution") == 2  # the dry run changed nothing

    first = migrate.migrate()
    assert first["pollution"] == 2
    assert first["pollution_duplicates_removed"] == 1
    assert store.count("pollution") == 3

    second = migrate.migrate()
    assert second["pollution"] == 0
    assert second["pollution_duplicates_removed"] == 0
    assert store.count("pollution") == 3


class _Collection:
    def __init__(self, fail_unique=False):
        self.operations, self.indexes, self.fail_unique = [], [], fail_unique

    def bulk_write(self, operations, ordered):
        self.operations.extend(operations)
        self.ordered = ordered
        return type("Result", (), {"upserted_count": len(operations)})()

    def create_index(self, keys, **kwargs):
        if kwargs.get("unique") and self.fail_unique:
            raise OperationFailure("E11000 duplicate key")
        self.indexes.append((keys, kwargs))


def _regular(monkeypatch, collection):
    class _Database(dict):
        def __missing__(self, name):
            return collection

    class _Client:
        def __init__(self, **kwargs):
            pass

        def get_default_database(self):
            return _Database()

    monkeypatch.setattr(repository_module, "MongoClient", _Client)
    return RegularRepository("mongodb://unused/db")


def test_the_mongo_insert_is_set_on_insert_by_sensor_and_hour(monkeypatch):
    collection = _Collection()
    regular = _regular(monkeypatch, collection)
    readings = [dict(row, sensorId="1000") for row in ROWS]

    assert regular.insert_missing("pollution", readings, READING_KEYS) == 3
    operation = collection.operations[0]
    assert operation._filter == {"sensorId": "1000", "time": 3600}
    assert set(operation._doc) == {"$setOnInsert"}  # never overwrites a stored hour
    assert operation._upsert is True
    assert collection.ordered is False


def test_duplicates_in_production_do_not_stop_the_app_from_starting(
    monkeypatch, caplog
):
    regular = _regular(monkeypatch, _Collection(fail_unique=True))
    with caplog.at_level("ERROR"):
        regular.ensure_indexes()  # must not raise
    assert any(
        "python -m worker.migrate" in record.message for record in caplog.records
    )

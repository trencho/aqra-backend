"""Tests for two ingest jobs in ``src/worker/ingest``.

``import_data`` carries a regression test for its walk + rmdir (bug A2): after importing
files it prunes empty leftover directories under ``DATA_EXTERNAL_PATH`` with
``root.rmdir()`` — but ``root`` is a ``str`` yielded by ``os.walk``, which has no
``rmdir``, so the job raised ``AttributeError`` the moment it met an empty directory. The
fix wraps it as ``Path(root).rmdir()``.

``fetch_locations`` carries the tests for the last-good-copy guard. ``fetch_countries``,
``fetch_cities`` and ``fetch_sensors`` each answer an upstream failure by logging and
returning ``[]``, and the job used to write that straight to disk — so one pulse.eco
outage emptied the location catalogue that ``read_cities`` and ``read_sensors`` serve, and
every downstream job then had nothing to iterate over until the next successful run.
"""

from json import dumps, loads

from repository import get_repository
from worker import ingest
from worker.ingest import fetch_locations, import_data


def test_import_data_removes_empty_external_subdirectory(tmp_path, monkeypatch):
    external = tmp_path / "external"
    empty_subdir = external / "skopje"
    empty_subdir.mkdir(parents=True)

    raw = tmp_path / "raw"
    raw.mkdir()

    # ``import_data`` reads these as module-level names, so patch them on the module.
    monkeypatch.setattr(ingest, "DATA_EXTERNAL_PATH", external)
    monkeypatch.setattr(ingest, "DATA_RAW_PATH", raw)

    # Must not raise (the pre-fix ``str.rmdir()`` raised AttributeError here).
    import_data()

    # The empty leftover directory was pruned; the external root is recreated at the end.
    assert not empty_subdir.exists()
    assert external.exists()


class _StubCache:
    """Records what the job publishes, so a test can tell "refreshed" from "left alone"."""

    def __init__(self):
        self.entries = {}

    def set(self, key, value):
        self.entries[key] = value


def _stub_locations(monkeypatch, tmp_path, *, countries, cities, sensors):
    """Point ``fetch_locations`` at temp storage and canned upstream responses.

    Returns the raw directory and the stub cache so a test can assert on both the file
    that survives a restart and the copy served in-process.
    """
    raw = tmp_path / "raw"
    raw.mkdir()
    cache = _StubCache()

    monkeypatch.setattr(ingest, "DATA_RAW_PATH", raw)
    monkeypatch.setattr(ingest, "cache", cache)
    monkeypatch.setattr(ingest, "fetch_countries", lambda: countries)
    monkeypatch.setattr(ingest, "fetch_cities", lambda: cities)
    monkeypatch.setattr(ingest, "fetch_sensors", lambda city_name: sensors)
    # The per-item repository writes are not what these tests are about, and a real
    # repository would need a database.
    monkeypatch.setattr(get_repository(), "save", lambda **kwargs: None, raising=False)
    return raw, cache


_COUNTRY = {"countryCode": "MK", "countryName": "Macedonia"}
_CITY = {"cityName": "skopje", "countryCode": "MK"}
_SENSOR = {"sensorId": "1000", "position": "41.99,21.43"}


def test_fetch_locations_writes_what_upstream_returned(tmp_path, monkeypatch):
    raw, cache = _stub_locations(
        monkeypatch,
        tmp_path,
        countries=[_COUNTRY],
        cities=[_CITY],
        sensors=[_SENSOR],
    )

    fetch_locations()

    assert loads((raw / "countries.json").read_text()) == [_COUNTRY]
    assert loads((raw / "cities.json").read_text()) == [_CITY]
    sensors = loads((raw / "skopje" / "sensors.json").read_text())
    assert [sensor["sensorId"] for sensor in sensors] == ["1000"]
    assert cache.entries["countries"] == [_COUNTRY]
    assert cache.entries["cities"] == [_CITY]


def test_fetch_locations_keeps_the_stored_catalogue_when_upstream_returns_nothing(
    tmp_path, monkeypatch
):
    # The failure this guards: every upstream call returns [] on error, so before the
    # guard this run overwrote three good files with "[]" and left the app with no
    # cities to serve until pulse.eco recovered.
    raw, cache = _stub_locations(
        monkeypatch, tmp_path, countries=[], cities=[], sensors=[]
    )
    (raw / "countries.json").write_text(dumps([_COUNTRY]))
    (raw / "cities.json").write_text(dumps([_CITY]))
    (raw / "skopje").mkdir()
    (raw / "skopje" / "sensors.json").write_text(dumps([_SENSOR]))

    fetch_locations()

    assert loads((raw / "countries.json").read_text()) == [_COUNTRY]
    assert loads((raw / "cities.json").read_text()) == [_CITY]
    assert loads((raw / "skopje" / "sensors.json").read_text()) == [_SENSOR]
    # ...and the in-process copy is not emptied either, which would have starved every
    # reader that prefers the cache over the file.
    assert cache.entries == {}


def test_fetch_locations_keeps_stored_sensors_when_only_that_call_fails(
    tmp_path, monkeypatch
):
    # Partial failure is the likelier shape: the city list arrives and the per-city
    # sensor call is the one that fails. The cities must still refresh.
    raw, cache = _stub_locations(
        monkeypatch, tmp_path, countries=[_COUNTRY], cities=[_CITY], sensors=[]
    )
    (raw / "skopje").mkdir()
    (raw / "skopje" / "sensors.json").write_text(dumps([_SENSOR]))

    fetch_locations()

    assert loads((raw / "cities.json").read_text()) == [_CITY]
    assert loads((raw / "skopje" / "sensors.json").read_text()) == [_SENSOR]


def test_fetch_locations_summarises_what_it_managed_to_save(
    tmp_path, monkeypatch, caplog
):
    # The end-to-end half of the tally: not that BatchTally counts (test_batch_tally.py
    # covers that) but that this job is actually wired to one, and that a run in which the
    # repository rejected everything is reported as ERROR rather than passing in silence.
    _stub_locations(
        monkeypatch, tmp_path, countries=[_COUNTRY], cities=[_CITY], sensors=[_SENSOR]
    )

    def refuse(**kwargs):
        raise RuntimeError("the database is gone")

    monkeypatch.setattr(get_repository(), "save", refuse, raising=False)

    with caplog.at_level("INFO", logger=ingest.__name__):
        fetch_locations()

    summaries = {
        record.message.split(":")[0]: record
        for record in caplog.records
        if record.name == ingest.__name__ and " -- " in record.message
    }
    # One per sub-batch: countries, cities and sensors fail for different reasons and are
    # counted apart so the log says which upstream is broken.
    assert set(summaries) == {
        "fetch_locations (countries)",
        "fetch_locations (cities)",
        "fetch_locations (sensors)",
    }
    for name, record in summaries.items():
        assert (
            record.levelname == "ERROR"
        ), f"{name} should escalate when all units fail"
        assert "0 done, 1 failed" in record.message


def test_fetch_locations_reports_a_clean_run_without_raising_the_level(
    tmp_path, monkeypatch, caplog
):
    # The other direction, so the assertion above cannot be satisfied by a tally that
    # always says ERROR.
    _stub_locations(
        monkeypatch, tmp_path, countries=[_COUNTRY], cities=[_CITY], sensors=[_SENSOR]
    )

    with caplog.at_level("INFO", logger=ingest.__name__):
        fetch_locations()

    summaries = [
        record
        for record in caplog.records
        if record.name == ingest.__name__ and " -- " in record.message
    ]
    assert len(summaries) == 3
    assert all(record.levelname == "INFO" for record in summaries)
    assert all("0 failed" in record.message for record in summaries)

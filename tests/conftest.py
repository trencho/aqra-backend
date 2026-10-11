"""Make the test suite hermetic: no network, no reliance on generated data.

Two things aren't committed (``data/`` is gitignored) yet the endpoint tests in
``test_api.py`` need them:

* **Raw location JSON** (``cities``/``countries``/``sensors``). In prod/CI these come
  from the worker's live ``fetch_locations`` job. Importing the app fetches nothing, and
  ``SKIP_DATA_FETCH`` keeps the worker's ``bootstrap`` offline too, so the suite seeds a
  trimmed skopje/MK/1000 slice from the committed fixtures under ``tests/fixtures/raw``.
* **Processed CSVs** for skopje/1000, which the history/pollutants endpoints read. All
  four data-dependent tests resolve to skopje/1000 (the coordinate tests use that
  sensor's exact position), so seeding one sensor is enough.

Seeding is **create-only**: we write a file only if it's absent and remove only the
files we created, so a developer's real local ``data/`` is never clobbered. Raw seeds
we create are left in place on teardown (``data/`` is gitignored, so leftovers never
dirty the tree and serve as a valid seed for the next run).
"""

from os import environ
from pathlib import Path
from shutil import copyfile, rmtree

import pytest

from definitions import DATA_PROCESSED_PATH, DATA_RAW_PATH, SKIP_DATA_FETCH

# Keep the worker's bootstrap offline if a test ever runs it unstubbed. ``setdefault`` so
# an explicit override from the environment still wins.
environ.setdefault(SKIP_DATA_FETCH, "1")

# The endpoint tests aren't exercising rate limits; disable the shared limiter so
# requests accumulated across a run can't trip a 429. ``tests/test_rate_limit.py``
# re-enables it per-test.
from api.config.limiter import limiter  # noqa: E402

limiter.enabled = False

_FIXTURES_RAW = Path(__file__).parent / "fixtures" / "raw"
_SENSOR_DIR = DATA_PROCESSED_PATH / "skopje" / "1000"

# Raw location fixtures → their destination under ``data/raw``.
_RAW_SEEDS = {
    _FIXTURES_RAW / "countries.json": DATA_RAW_PATH / "countries.json",
    _FIXTURES_RAW / "cities.json": DATA_RAW_PATH / "cities.json",
    _FIXTURES_RAW
    / "skopje"
    / "sensors.json": DATA_RAW_PATH
    / "skopje"
    / "sensors.json",
}

# Minimal processed frames: a ``time`` column (history filters on it) plus pollutant
# columns (pollutants lists the ones present). One row is enough — the tests assert 200.
_SEED_FILES = {
    "pollution.csv": "time,pm2_5,pm10,no2,o3,so2,co\n1704067200,12.0,20.0,15.0,40.0,5.0,0.4\n",
    "weather.csv": "time,temperature,humidity,pressure\n1704067200,7.5,60.0,1013.0\n",
}

# Files we actually created this run, so teardown only removes our own.
_created_processed: list[Path] = []


def pytest_configure(config: pytest.Config) -> None:
    # Drop any stale FileSystemCache entries (the app caches at /tmp/cache) so a
    # previously cached "data missing" 404 for skopje/1000 can't mask the seed.
    rmtree(Path("/tmp/cache"), ignore_errors=True)

    # Seed raw location JSON only where it's missing, so a real local data/raw stays
    # untouched. Left in place on teardown (data/ is gitignored).
    for source, destination in _RAW_SEEDS.items():
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            copyfile(source, destination)

    _SENSOR_DIR.mkdir(parents=True, exist_ok=True)
    for name, content in _SEED_FILES.items():
        target = _SENSOR_DIR / name
        if not target.exists():
            target.write_text(content)
            _created_processed.append(target)


def pytest_unconfigure(config: pytest.Config) -> None:
    # Under xdist, clean up only on the controller (workers have ``workerinput``),
    # which finishes after all workers — so the files stay in place while tests run.
    if hasattr(config, "workerinput"):
        return

    for target in _created_processed:
        target.unlink(missing_ok=True)
    # Prune the now-empty dirs we created, stopping at data/processed.
    for directory in (_SENSOR_DIR, _SENSOR_DIR.parent):
        try:
            directory.rmdir()
        except OSError:
            break


@pytest.fixture
def publish_model(tmp_path, monkeypatch):
    """Publish a trained model through the registry under a temp MODELS_PATH.

    Returns ``publish(city, sensor, pollutant, model, features, scaler, metrics=None)``,
    which writes a run exactly as training does (model first, manifest last) and moves the
    ``current`` pointer to it, returning the run directory.
    """
    from modeling.train_model import save_pipeline
    from models import registry

    models_path = tmp_path / "models"
    monkeypatch.setattr(registry, "MODELS_PATH", models_path)
    registry.load_run.cache_clear()

    def publish(city, sensor, pollutant, model, features, scaler, metrics=None):
        key = registry.ModelKey(city, sensor, pollutant)
        run_dir = registry.new_run_dir(key)
        model.save(run_dir)
        save_pipeline(run_dir, features, scaler, type(model).__name__, metrics=metrics)
        assert registry.publish(key, run_dir, verify=registry.load_run)
        return run_dir

    publish.models_path = models_path
    yield publish
    registry.load_run.cache_clear()

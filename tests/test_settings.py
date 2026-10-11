"""Settings are read from the environment at call time, with types and defaults."""

import pytest

import repository as repository_module
from definitions import (
    APP_ENV,
    COUNTRIES,
    ENABLED_COUNTRIES,
    ENVIRONMENT_VARIABLES,
    MONGO_DATABASE,
    MONGO_PASSWORD,
    MONGO_USERNAME,
    MONGODB_CONNECTION,
    MONGODB_HOSTNAME,
    SKIP_DATA_FETCH,
    TRAINING_JOBS,
    TRUSTED_PROXY_HOPS,
)
from settings import get_settings, missing_required_variables

_MONGO = {
    MONGODB_CONNECTION: "mongodb",
    MONGO_USERNAME: "user",
    MONGO_PASSWORD: "secret",
    MONGODB_HOSTNAME: "mongo:27017",
    MONGO_DATABASE: "air-quality",
}


@pytest.fixture
def clean_env(monkeypatch):
    for name in [
        *ENVIRONMENT_VARIABLES,
        ENABLED_COUNTRIES,
        TRAINING_JOBS,
        TRUSTED_PROXY_HOPS,
    ]:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_defaults_describe_a_development_process(clean_env):
    settings = get_settings()
    assert settings.app_env == "development"
    assert not settings.is_production
    assert settings.trusted_proxy_hops == 1
    assert settings.enabled_countries == frozenset(COUNTRIES)
    assert settings.mongo_uri is None
    assert settings.training_jobs >= 1


def test_values_are_read_at_call_time_not_import_time(clean_env):
    clean_env.setenv(APP_ENV, "production")
    clean_env.setenv(TRUSTED_PROXY_HOPS, "2")
    clean_env.setenv(SKIP_DATA_FETCH, "TRUE")
    settings = get_settings()
    assert settings.is_production
    assert settings.trusted_proxy_hops == 2
    assert settings.skip_data_fetch


def test_enabled_countries_are_trimmed_and_upper_cased(clean_env):
    clean_env.setenv(ENABLED_COUNTRIES, " mk, rs ,,bg")
    assert get_settings().enabled_countries == {"MK", "RS", "BG"}


def test_training_jobs_never_drops_below_one(clean_env):
    clean_env.setenv(TRAINING_JOBS, "0")
    assert get_settings().training_jobs == 1


def test_mongo_uri_needs_every_part(clean_env):
    for name, value in _MONGO.items():
        clean_env.setenv(name, value)
    assert get_settings().mongo_uri == (
        "mongodb://user:secret@mongo:27017/air-quality"
        "?authSource=admin&retryWrites=true&w=majority"
    )
    clean_env.delenv(MONGO_PASSWORD)
    assert get_settings().mongo_uri is None


def test_missing_required_variables_lists_them_in_order(clean_env):
    assert missing_required_variables() == ENVIRONMENT_VARIABLES
    clean_env.setenv(APP_ENV, "production")
    assert APP_ENV not in missing_required_variables()


def test_constructing_the_mongo_repository_sends_no_commands(monkeypatch):
    # Index creation used to run in the constructor, so resolving the repository at import
    # time talked to Mongo before the app existed. It is ensure_indexes() now, called once
    # by the factory.
    calls = []

    class _Collection:
        def create_index(self, keys, **kwargs):
            calls.append(keys)

    class _Database(dict):
        def __missing__(self, name):
            return _Collection()

    class _Client:
        def __init__(self, **kwargs):
            pass

        def get_default_database(self):
            return _Database()

    monkeypatch.setattr(repository_module, "MongoClient", _Client)
    regular = repository_module.RegularRepository("mongodb://unused/db")
    assert calls == []
    regular.ensure_indexes()
    assert ("sensorId", 1) in [key for keys in calls for key in keys]

"""Typed runtime settings, read from the environment at the moment they are needed.

`definitions.py` keeps the env-var NAMES and the filesystem paths; this module turns the values
into one frozen object with types and defaults, so a caller asks `get_settings().redis_url`
instead of repeating `environ.get(REDIS_URL)` and its fallback at every site.

Read at use time, never cached at import: the tests change the environment per test, and a value
frozen when the module loaded would ignore them.
"""

from dataclasses import dataclass
from os import cpu_count, environ

from definitions import (
    APP_ENV,
    COUNTRIES,
    ENABLED_COUNTRIES,
    ENV_DEV,
    ENV_PROD,
    ENVIRONMENT_VARIABLES,
    GITHUB_TOKEN,
    MONGO_DATABASE,
    MONGO_PASSWORD,
    MONGO_USERNAME,
    MONGODB_CONNECTION,
    MONGODB_HOSTNAME,
    OPEN_WEATHER_TOKEN,
    QUEUE_REDIS_URL,
    RATE_LIMIT_STORAGE_URI,
    REDIS_URL,
    REPO_NAME,
    SKIP_DATA_FETCH,
    TRAINING_JOBS,
    TRUSTED_PROXY_HOPS,
)

TRUTHY = ("1", "true", "yes")


@dataclass(frozen=True)
class Settings:
    app_env: str
    redis_url: str | None
    rate_limit_storage_uri: str | None
    queue_redis_url: str | None
    trusted_proxy_hops: int
    skip_data_fetch: bool
    enabled_countries: frozenset[str]
    training_jobs: int
    open_weather_token: str | None
    github_token: str | None
    repo_name: str | None
    mongo_uri: str | None

    @property
    def is_production(self) -> bool:
        return self.app_env == ENV_PROD

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_env=environ.get(APP_ENV, ENV_DEV),
            redis_url=environ.get(REDIS_URL) or None,
            rate_limit_storage_uri=environ.get(RATE_LIMIT_STORAGE_URI) or None,
            queue_redis_url=environ.get(QUEUE_REDIS_URL) or None,
            trusted_proxy_hops=int(environ.get(TRUSTED_PROXY_HOPS, "1")),
            skip_data_fetch=environ.get(SKIP_DATA_FETCH, "").lower() in TRUTHY,
            enabled_countries=_enabled_countries(environ.get(ENABLED_COUNTRIES)),
            training_jobs=_training_jobs(),
            open_weather_token=environ.get(OPEN_WEATHER_TOKEN) or None,
            github_token=environ.get(GITHUB_TOKEN) or None,
            repo_name=environ.get(REPO_NAME) or None,
            mongo_uri=_mongo_uri(),
        )


def get_settings() -> Settings:
    return Settings.from_env()


def missing_required_variables() -> list[str]:
    """The production variables that are absent, in declaration order."""
    return [name for name in ENVIRONMENT_VARIABLES if environ.get(name) is None]


def _enabled_countries(configured: str | None) -> frozenset[str]:
    # Every supported country unless ENABLED_COUNTRIES narrows it.
    if configured:
        return frozenset(
            code.strip().upper() for code in configured.split(",") if code.strip()
        )
    return frozenset(COUNTRIES)


def _training_jobs() -> int:
    # Half the CPUs unless TRAINING_JOBS says otherwise, and never fewer than one.
    return max(1, int(environ.get(TRAINING_JOBS, (cpu_count() or 2) // 2)))


def _mongo_uri() -> str | None:
    parts = [
        environ.get(name)
        for name in (
            MONGODB_CONNECTION,
            MONGO_USERNAME,
            MONGO_PASSWORD,
            MONGODB_HOSTNAME,
            MONGO_DATABASE,
        )
    ]
    if None in parts:
        return None
    connection, username, password, hostname, database = parts
    return (
        f"{connection}://{username}:{password}@{hostname}/{database}"
        "?authSource=admin&retryWrites=true&w=majority"
    )

from atexit import register

from flask import Flask

from caching import cache
from settings import get_settings


def _cache_config() -> dict:
    # Prefer a shared Redis cache when a URL is configured (consistent across gunicorn
    # workers and k8s pods); otherwise fall back to the per-host filesystem cache.
    if redis_url := get_settings().redis_url:
        return {
            "CACHE_TYPE": "RedisCache",
            "CACHE_REDIS_URL": redis_url,
            "CACHE_KEY_PREFIX": "aqra:",
        }
    return {"CACHE_TYPE": "FileSystemCache", "CACHE_DIR": "/tmp/cache"}


def init_cache(app: Flask) -> None:
    config = _cache_config()
    cache.init_app(app, config)
    # Only tidy up the local filesystem cache at exit; clearing a shared Redis on every
    # worker/pod shutdown would wipe entries other processes still rely on.
    if config["CACHE_TYPE"] == "FileSystemCache":
        register(cache.clear)

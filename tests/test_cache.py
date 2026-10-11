"""Hermetic tests for cache backend selection (``api.config.cache``).

Verifies the env-driven choice between a shared Redis cache and the filesystem
fallback without needing a running Redis (the config dict is asserted directly).
"""

# Import the config package first so the api chain initialises in order.
import api.config  # noqa: F401
from caching import cache_config
from definitions import REDIS_URL


def test_cache_config_uses_redis_when_url_set(monkeypatch):
    monkeypatch.setenv(REDIS_URL, "redis://cache:6379/0")
    config = cache_config()
    assert config["CACHE_TYPE"] == "RedisCache"
    assert config["CACHE_REDIS_URL"] == "redis://cache:6379/0"
    assert config["CACHE_KEY_PREFIX"] == "aqra:"


def test_cache_config_falls_back_to_filesystem(monkeypatch):
    monkeypatch.delenv(REDIS_URL, raising=False)
    config = cache_config()
    assert config["CACHE_TYPE"] == "FileSystemCache"
    assert config["CACHE_DIR"] == "/tmp/cache"

"""The shared cache instance, owned by no layer.

`processing` and `preparation` memoize expensive reads, and the object they memoize with used to
live in `api/config/cache.py`. That made the two non-HTTP layers import the HTTP layer, and the
cost was concrete: importing anything under `modeling` or `processing` on its own pulled in the
whole Flask app and died on a circular import, so the training code could not be exercised or
tested without booting the service.

The Flask wiring (`init_cache`, the Redis-or-filesystem choice, the atexit hook) stays in
`api/config/cache.py`, which is where it belongs. Only the instance lives here, above every layer
that needs it.
"""

from flask_caching import Cache

from settings import get_settings

cache = Cache()


def cache_config() -> dict:
    # Prefer a shared Redis cache when a URL is configured (consistent across gunicorn
    # workers, the RQ worker and k8s pods); otherwise fall back to the per-host filesystem
    # cache.
    if redis_url := get_settings().redis_url:
        return {
            "CACHE_TYPE": "RedisCache",
            "CACHE_REDIS_URL": redis_url,
            "CACHE_KEY_PREFIX": "aqra:",
        }
    return {"CACHE_TYPE": "FileSystemCache", "CACHE_DIR": "/tmp/cache"}

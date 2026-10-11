"""The Flask app context the jobs run in.

The jobs call memoized functions and read `cache.get("cities")`, and Flask-Caching only works
inside an application context. The worker builds a minimal app that carries nothing but the
cache, configured exactly as the API's, so a value one process caches the other can read when
both point at the same Redis.
"""

from functools import cache as once
from functools import wraps

from flask import Flask, has_app_context

from caching import cache, cache_config


@once
def worker_app() -> Flask:
    app = Flask("worker")
    cache.init_app(app, cache_config())
    return app


def in_app_context(job):
    """Run `job` inside the worker's app context, entering it only if none is active."""

    @wraps(job)
    def wrapper(*args, **kwargs):
        if has_app_context():
            return job(*args, **kwargs)
        with worker_app().app_context():
            return job(*args, **kwargs)

    return wrapper

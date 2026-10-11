from atexit import register

from flask import Flask

from caching import cache, cache_config


def init_cache(app: Flask) -> None:
    config = cache_config()
    cache.init_app(app, config)
    # Only tidy up the local filesystem cache at exit; clearing a shared Redis on every
    # worker/pod shutdown would wipe entries other processes still rely on.
    if config["CACHE_TYPE"] == "FileSystemCache":
        register(cache.clear)

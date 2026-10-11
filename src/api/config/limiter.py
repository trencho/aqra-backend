from os import environ

from flask import Flask, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

from definitions import (
    APP_ENV,
    ENV_DEV,
    ENV_PROD,
    METRICS_PATH,
    RATE_LIMIT_DEFAULTS,
    RATE_LIMIT_STORAGE_URI,
    REDIS_URL,
    TRUSTED_PROXY_HOPS,
    URL_PREFIX,
)


def _storage_uri() -> str:
    # A dedicated override wins; otherwise share the Redis backend when one is
    # configured; otherwise in-process memory (dev / single worker).
    return environ.get(RATE_LIMIT_STORAGE_URI) or environ.get(REDIS_URL) or "memory://"


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=RATE_LIMIT_DEFAULTS,
    storage_uri=_storage_uri(),
    swallow_errors=True,  # a storage-backend hiccup must not 500 the request
)


@limiter.request_filter
def _exempt_monitoring_endpoints() -> bool:
    # Health probes (k8s) and the Prometheus scrape endpoint are hit frequently and
    # must never be throttled.
    return (
        request.path.startswith(f"{URL_PREFIX}/healthz") or request.path == METRICS_PATH
    )


def trusted_proxy_hops() -> int:
    return int(environ.get(TRUSTED_PROXY_HOPS, "1"))


def trust_proxies(app: Flask, hops: int) -> None:
    # Behind nginx over a unix socket, request.remote_addr is the socket peer, not the client.
    # ProxyFix takes the client from X-Forwarded-For, counting `hops` entries from the right.
    # In Kubernetes the header arrives as "client, ingress-ip" because the pod's nginx appends
    # the ingress controller it heard from, so trusting one hop keyed every visitor on the
    # ingress controller's IP and they all shared one rate-limit bucket.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops)


def init_limiter(app: Flask) -> None:
    if environ.get(APP_ENV, ENV_DEV) == ENV_PROD:
        trust_proxies(app, trusted_proxy_hops())
    limiter.init_app(app)

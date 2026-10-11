"""Hermetic tests for rate limiting (``api.config.limiter``).

The shared limiter is disabled for the rest of the suite (see ``conftest.py``); these
tests re-enable it on throwaway apps and assert enforcement + the health-check
exemption. Each test resets the in-memory buckets after wiring the app so the shared
client IP doesn't leak request counts.
"""

import pytest
from flask import Flask

# Import the config package first so the api chain initialises in order.
import api.config  # noqa: F401
from api.blueprints.errors import errors_blueprint
from api.config.limiter import (
    _storage_uri,
    init_limiter,
    limiter,
    trust_proxies,
    trusted_proxy_hops,
)
from definitions import (
    RATE_LIMIT_STORAGE_URI,
    REDIS_URL,
    TRUSTED_PROXY_HOPS,
    URL_PREFIX,
)


@pytest.fixture
def active_limiter():
    was_enabled = limiter.enabled
    limiter.enabled = True
    yield
    limiter.enabled = was_enabled


def test_rate_limit_returns_429_after_limit(active_limiter):
    app = Flask(__name__)

    @app.route("/ping")
    @limiter.limit("2 per minute")
    def _ping():
        return "ok"

    init_limiter(app)
    limiter.reset()  # storage exists once init_app has run; clear leaked counts
    client = app.test_client()

    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 429


def test_health_checks_are_exempt_from_limits(active_limiter):
    app = Flask(__name__)

    @app.route(f"{URL_PREFIX}/healthz/live")
    @limiter.limit("1 per minute")
    def _live():
        return "ok"

    @app.route("/data")
    @limiter.limit("1 per minute")
    def _data():
        return "ok"

    init_limiter(app)
    limiter.reset()
    client = app.test_client()

    # A normal route is limited after its allowance...
    assert client.get("/data").status_code == 200
    assert client.get("/data").status_code == 429
    # ...but the health path is exempt no matter how often it is hit.
    for _ in range(3):
        assert client.get(f"{URL_PREFIX}/healthz/live").status_code == 200


def test_rate_limited_response_is_429_with_error_blueprint(active_limiter):
    # The real app registers the errors blueprint, whose catch-all handler must not
    # turn the limiter's 429 into a 500 (the bug the end-to-end check caught).
    app = Flask(__name__)
    app.register_blueprint(errors_blueprint)

    @app.route("/guarded")
    @limiter.limit("1 per minute")
    def _guarded():
        return "ok"

    init_limiter(app)
    limiter.reset()
    client = app.test_client()

    assert client.get("/guarded").status_code == 200
    assert client.get("/guarded").status_code == 429


def test_storage_uri_prefers_override_then_redis_then_memory(monkeypatch):
    monkeypatch.delenv(RATE_LIMIT_STORAGE_URI, raising=False)
    monkeypatch.delenv(REDIS_URL, raising=False)
    assert _storage_uri() == "memory://"

    monkeypatch.setenv(REDIS_URL, "redis://shared:6379")
    assert _storage_uri() == "redis://shared:6379"

    monkeypatch.setenv(RATE_LIMIT_STORAGE_URI, "redis://dedicated:6379")
    assert _storage_uri() == "redis://dedicated:6379"


def _client_key(hops: int, forwarded_for: str) -> str:
    """What the limiter keys on for a request that arrives through ``hops`` proxies."""
    app = Flask(__name__)

    @app.route("/whoami")
    def _whoami():
        return limiter._key_func()

    trust_proxies(app, hops)
    return (
        app.test_client()
        .get(
            "/whoami",
            headers={"X-Forwarded-For": forwarded_for},
            environ_base={"REMOTE_ADDR": "unix-socket-peer"},
        )
        .get_data(as_text=True)
    )


def test_two_proxies_key_on_the_client_not_the_ingress():
    # Kubernetes: ingress-nginx sets "client", the pod's nginx appends the ingress IP.
    assert _client_key(2, "203.0.113.9, 10.0.0.1") == "203.0.113.9"


def test_one_trusted_hop_behind_two_proxies_keys_on_the_ingress():
    # The bug this replaced: every visitor shared the ingress controller's bucket.
    assert _client_key(1, "203.0.113.9, 10.0.0.1") == "10.0.0.1"


def test_a_spoofed_leading_entry_is_not_trusted():
    # A client can prepend anything; only the entries the proxies appended count.
    assert _client_key(2, "6.6.6.6, 203.0.113.9, 10.0.0.1") == "203.0.113.9"


def test_trusted_proxy_hops_defaults_to_one(monkeypatch):
    monkeypatch.delenv(TRUSTED_PROXY_HOPS, raising=False)
    assert trusted_proxy_hops() == 1
    monkeypatch.setenv(TRUSTED_PROXY_HOPS, "2")
    assert trusted_proxy_hops() == 2

"""
Application configuration constants and paths.
This module defines all environment variables and file paths used throughout the application.
"""

from os import environ
from pathlib import Path

# Environment variable keys
APP_ENV = "APP_ENV"
# When truthy, create_app() skips the upstream location fetch on startup so the app
# can be imported without network access (the test suite sets it — see tests/conftest.py).
SKIP_DATA_FETCH = "SKIP_DATA_FETCH"
GITHUB_TOKEN = "GITHUB_TOKEN"
MONGO_DATABASE = "MONGO_DATABASE"
MONGO_PASSWORD = "MONGO_PASSWORD"
MONGO_USERNAME = "MONGO_USERNAME"
MONGODB_CONNECTION = "MONGODB_CONNECTION"
MONGODB_HOSTNAME = "MONGODB_HOSTNAME"
OPEN_WEATHER = "open_weather"
OPEN_WEATHER_TOKEN = "OPEN_WEATHER_TOKEN"
# Optional shared Redis backend for the cache + rate limiter (multi-worker/pod prod).
# When unset, the cache falls back to a per-host filesystem cache and the limiter to
# in-process memory.
REDIS_URL = "REDIS_URL"
# Redis for the RQ job queue. Separate from REDIS_URL on purpose: the cache Redis evicts keys
# (allkeys-lru, no persistence), and an evicted job is a job that silently never runs.
QUEUE_REDIS_URL = "QUEUE_REDIS_URL"
REPO_NAME = "REPO_NAME"
# Threads one training job may use (hyperparameter search and the estimator together). Defaults
# to half the CPUs, which is what the search used before it was configurable.
TRAINING_JOBS = "TRAINING_JOBS"
VOLUME_PATH = "VOLUME_PATH"

# Environment variable validation list
ENVIRONMENT_VARIABLES = [
    APP_ENV,
    GITHUB_TOKEN,
    MONGO_DATABASE,
    MONGO_PASSWORD,
    MONGO_USERNAME,
    MONGODB_CONNECTION,
    MONGODB_HOSTNAME,
    OPEN_WEATHER_TOKEN,
    REPO_NAME,
]

# Path configuration
ROOT_PATH = Path(environ.get(VOLUME_PATH, str(Path(__file__).resolve().parent)))
DATA_PATH = ROOT_PATH / "data"
LOG_PATH = ROOT_PATH / "logs"
MODELS_PATH = ROOT_PATH / "models"
RESULTS_PATH = ROOT_PATH / "results"

# Data subdirectories
DATA_EXTERNAL_PATH = DATA_PATH / "external"
DATA_PROCESSED_PATH = DATA_PATH / "processed"
DATA_RAW_PATH = DATA_PATH / "raw"

# Results subdirectories
RESULTS_ERRORS_PATH = RESULTS_PATH / "errors"
RESULTS_ERRORS_PLOTS_PATH = RESULTS_ERRORS_PATH / "plots"
RESULTS_PREDICTIONS_PATH = RESULTS_PATH / "predictions"
RESULTS_PREDICTIONS_PLOTS_PATH = RESULTS_PREDICTIONS_PATH / "plots"

# Environment modes
ENV_DEV = "development"
ENV_PROD = "production"

# Data configuration
COLLECTIONS = ["weather", "pollution"]
CHUNK_SIZE = 15000

# Cache timeouts in seconds
CACHE_TIMEOUTS = {
    "never": 0,
    "1min": 60,
    "3min": 180,
    "5min": 300,
    "1h": 3600,
}

# Country configuration. COUNTRIES is the full catalogue of pulse.eco-supported
# countries (the upstream data source). The active subset defaults to all of them and
# can be narrowed with the ENABLED_COUNTRIES env var (comma-separated ISO codes, e.g.
# "MK,RS,BG") — useful to bound the scheduler's fetch/train workload.
ENABLED_COUNTRIES = "ENABLED_COUNTRIES"
COUNTRIES = {
    "AL": "Albania",
    "BG": "Bulgaria",
    "CH": "Switzerland",
    "CY": "Cyprus",
    "DE": "Germany",
    "DK": "Denmark",
    "GR": "Greece",
    "HR": "Croatia",
    "IE": "Ireland",
    "MD": "Moldova",
    "MK": "Macedonia",
    "NL": "Netherlands",
    "RO": "Romania",
    "RS": "Serbia",
    "US": "USA",
}

# Pollutant types and mappings
# Version of the on-disk model artefact set (model + selected_features + scaler + manifest).
# Bumping this retires every previously trained artefact: the forecast loader refuses a
# directory whose manifest carries a different schema, so the scheduler retrains it rather
# than serving numbers produced by an older pipeline.
PIPELINE_SCHEMA = 1

POLLUTANTS = {
    "aqi": "AQI",
    "co": "CO",
    "nh3": "NH3",
    "no": "NO",
    "no2": "NO2",
    "o3": "O3",
    "pm2_5": "PM2.5",
    "pm10": "PM10",
    "so2": "SO2",
}

# Enabled regression models
REGRESSION_MODELS = {
    "LightGBMRegressionModel": "LightGBM",
    "RandomForestRegressionModel": "Random Forest",
    "XGBoostRegressionModel": "XGBoost",
}

# API configuration
URL_PREFIX = "/api/v1"

# Rate limiting (Flask-Limiter). Default limits apply to every API route (health
# checks are exempt). Storage defaults to in-process memory, which is fine for dev and
# a single worker; multi-worker prod needs a shared backend (e.g. Redis) — override
# with the RATE_LIMIT_STORAGE_URI env var (e.g. "redis://host:6379").
RATE_LIMIT_STORAGE_URI = "RATE_LIMIT_STORAGE_URI"
RATE_LIMIT_DEFAULTS = ["1000 per hour", "60 per minute"]
# How many reverse proxies in front of gunicorn append to X-Forwarded-For. Kubernetes has two
# (ingress-nginx, then the pod's own nginx), compose has one. ProxyFix trusts exactly this many
# hops, so too low keys every client on a proxy's IP and too high lets a client spoof its own.
TRUSTED_PROXY_HOPS = "TRUSTED_PROXY_HOPS"

# Prometheus metrics (prometheus-flask-exporter). Metrics are per-process; multi-worker
# prod aggregation needs PROMETHEUS_MULTIPROC_DIR set plus a gunicorn worker hook.
METRICS_PATH = "/metrics"

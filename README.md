# aqra-backend (AQRA)

A Flask REST API that collects air-quality and weather data per city/sensor, trains machine-learning
regression models on it, and serves **current readings, history, pollutant metrics, and multi-model
forecasts**: with interactive Swagger docs and prediction/error plots. It covers the pulse.eco
sensor network across all 15 supported countries by default (North Macedonia, Serbia, Bulgaria,
Greece, Romania, Switzerland, and more); narrow the coverage with the `ENABLED_COUNTRIES` env var.

## Feature overview

- **Locations**: list countries and cities, and the sensors within a city (by id or nearest to a
  coordinate).
- **History**: time-series weather or pollution readings for a city/sensor or a coordinate.
- **Pollutants**: which pollutants a city/sensor or coordinate reports, out of
  `AQI, CO, NH3, NO, NO2, O3, PM2.5, PM10, SO2` (names and keys, not readings; readings come from
  History).
- **Forecasts**: model-driven pollutant predictions for a city, a specific sensor, or a coordinate.
- **Evaluation**: the model serving each pollutant's forecast and its held-out MAE, MAPE, MSE and
  RMSE.
- **Plots**: rendered prediction and error plots per city/sensor/pollutant.
- **Ops**: OpenAPI/Swagger UI, liveness/readiness health checks, CORS, caching (Redis or
  filesystem), per-client rate limiting, Prometheus metrics at `/metrics` (not routed by the
  ingress), and (in prod) an APScheduler pipeline that fetches data, trains models, and publishes
  forecasts on a cron.

## Tech stack

- **Python** (3.14-slim in the prod image) · **Flask** · **Waitress** (dev) / **Gunicorn + nginx** (prod).
- **flasgger** (OpenAPI/Swagger) · **Flask-Caching** · **Flask-Cors** · **flask-healthz** · **APScheduler**.
- **pandas / numpy / scipy / scikit-learn / statsmodels** and the model backends
  **LightGBM / Random Forest / XGBoost**; **matplotlib / seaborn** for plots.
- **MongoDB** (via **pymongo**) in prod; an in-memory repository in dev. **SQLAlchemy** backs the
  APScheduler job store. Note the two production paths run **different MongoDB majors**: the
  compose stack builds `aqra-mongo` from `mongo:8.3.4`, while the Kubernetes deployment pins
  `mongo:4.4` because the node lacks AVX, which MongoDB 5+ requires.
- **Black** (formatting) · **Ruff** (import hygiene and order) · **pytest** + **Flask-Testing**
  (tests).

## API

All routes are prefixed with **`/api/v1`**. Swagger UI: **`/api/v1/apidocs/`**.

| Area       | Endpoint                                                                              |
|------------|---------------------------------------------------------------------------------------|
| Countries  | `GET /countries/`, `GET /countries/<country_code>/`                                    |
| Cities     | `GET /cities/`, `GET /cities/<city_name>/`                                             |
| Sensors    | `GET /cities/<city_name>/sensors/`, `GET /cities/<city_name>/sensors/<sensor_id>/`     |
| History    | `GET /cities/<city_name>/sensors/<sensor_id>/history/<data_type>/` · `GET /coordinates/<lat>,<lon>/history/<data_type>/` |
| Pollutants | `GET /cities/<city_name>/sensors/<sensor_id>/pollutants/` · `GET /coordinates/<lat>,<lon>/pollutants/` |
| Forecast   | `GET /cities/<city_name>/forecast/` · `GET /cities/<city_name>/sensors/<sensor_id>/forecast/` · `GET /cities/coordinates/<lat>,<lon>/forecast/` · `GET /sensors/coordinates/<lat>,<lon>/forecast/` |
| Evaluation | `GET /cities/<city_name>/sensors/<sensor_id>/evaluation/` · `GET /cities/<city_name>/sensors/<sensor_id>/pollutants/<pollutant>/evaluation/` (best model + MAE/MAPE/MSE/RMSE) |
| Plots      | `GET /plots/predictions/cities/<city_name>/sensors/<sensor_id>/pollutants/<pollutant>/` · `GET /plots/errors/cities/<city_name>/sensors/<sensor_id>/pollutants/<pollutant>/errors/<error_type>/` |
| Health     | `GET /healthz/live`, `GET /healthz/ready`                                              |

`data_type` is `weather` or `pollution`. Data fetching and model training run only on the
scheduler; no HTTP route triggers them.

## Project structure

```text
definitions.py             # env-var keys, filesystem paths, and constants (POLLUTANTS, REGRESSION_MODELS, URL_PREFIX…)
src/
├── api/
│   ├── app.py             # entry point (waitress serve)
│   ├── config/            # create_app() factory + init_* (blueprints, cache, cors, health, swagger, scheduler, repository…)
│   └── blueprints/        # one dir per resource: <name>.py (+ *.yml flasgger specs) — registered in config/blueprints.py
├── preparation/           # location + weather data fetchers (upstream APIs)
├── processing/            # feature generation / imputation / scaling / selection, forecasting, index calc
├── modeling/              # train + evaluate regression models
├── models/                # model implementations (base + LightGBM / RandomForest / XGBoost enabled)
├── visualization/         # prediction & error plots
└── utils.py
data/  models/  results/   # runtime artifacts, all generated and gitignored
docker/  kubernetes/       # deployment
tests/                     # pytest (Flask-Testing) endpoint tests
```

## Architecture

- **App factory**: `src/api/config/__init__.py:create_app()` composes small `init_*` functions
  (logger, GC, system paths, converters, blueprints, cache, CORS, health, limiter, metrics,
  swagger, data).
- **Repository**: `src/repository.py` exposes a `Repository` interface with two implementations
  selected by `APP_ENV`: `RegularRepository` (MongoDB) in prod, `InMemoryRepository` in dev/tests.
- **Scheduler** (prod only): APScheduler cron jobs fetch hourly data, dump/back up data to a Git
  repo, train models, and publish forecasts; the job store is SQLite via SQLAlchemy.
- **Data pipeline**: `preparation` (fetch) → `processing` (clean/feature-engineer) → `modeling`
  (train/evaluate) → forecasts + `visualization` plots.

## Environment configuration

Selected by **`APP_ENV`** (`development`, default, or `production`). In production the app validates
the required variables at startup and exits if any are missing.

| Variable                                                              | Purpose                                             |
|----------------------------------------------------------------------|-----------------------------------------------------|
| `APP_ENV`                                                            | `development` (in-memory repo, no scheduler) or `production` |
| `ENABLED_COUNTRIES`                                                  | optional comma-separated ISO country codes to cover (e.g. `MK,RS,BG`); defaults to all 15 supported |
| `MONGODB_CONNECTION` / `MONGODB_HOSTNAME` / `MONGO_DATABASE` / `MONGO_USERNAME` / `MONGO_PASSWORD` | MongoDB datasource (prod) |
| `OPEN_WEATHER_TOKEN`                                                 | upstream weather/air-quality API token              |
| `GITHUB_TOKEN` / `REPO_NAME`                                         | data-dump backup to a Git repository (scheduler)    |
| `REDIS_URL`                                                          | optional shared Redis for the cache + rate limiter (multi-worker/pod); falls back to a filesystem cache + in-memory limits when unset |
| `RATE_LIMIT_STORAGE_URI`                                             | optional limiter backend override; wins over `REDIS_URL` |
| `SKIP_DATA_FETCH`                                                    | `1`/`true`/`yes` skips the startup fetch from pulse.eco, so the app boots offline (the tests set it) |
| `VOLUME_PATH`                                                        | root for `data/`, `logs/`, `models/`, `results/` (defaults to the project dir) |

## Running

### Local (dev)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install --no-cache-dir -r requirements/dev.txt
SKIP_DATA_FETCH=1 python src/api/app.py                 # http://127.0.0.1:5000  (docs at /api/v1/apidocs/)
```

Dev uses the in-memory repository (no MongoDB required). `APP_ENV` defaults to `development`.
`create_app()` fetches upstream location data from pulse.eco on startup: set `SKIP_DATA_FETCH=1`
to run offline. Data-dependent endpoints (history/pollutants/forecast/plots) return 404 until
`data/processed/<city>/<sensor>/*.csv` exist (in production the scheduler generates these; `data/`
is gitignored, so nothing is seeded by default locally).

### Docker

```bash
cd docker
# create docker/.env with the production values (MONGO_*, OPEN_WEATHER_TOKEN, GITHUB_TOKEN, REPO_NAME, …);
# compose reads docker/.env
docker compose up --build
```

## Testing

```bash
pytest            # tests/ run in parallel (-n logical); see pytest.ini
```

The tests are endpoint integration tests (Flask-Testing) plus unit tests over the processing/modeling
layers, run against the in-memory repository. The suite is **hermetic**: `tests/conftest.py` sets
`SKIP_DATA_FETCH=1` (so `create_app()` does not fetch upstream data), disables the rate limiter, and
seeds a trimmed skopje/MK fixture from `tests/fixtures/raw` into the gitignored `data/` dir. Seeding
only creates files that are missing, so your own `data/` is never overwritten; the processed seeds
are removed on teardown and the raw ones stay. No network or MongoDB required.

## Formatting

```bash
black src tests definitions.py           # format
black --check src tests definitions.py   # verify (CI runs this)
ruff check src tests definitions.py      # unused imports and import order (CI runs this too)
```

A pre-commit hook runs Ruff's import fixes and Black on the Python files you stage. Enable it once
per clone:

```bash
git config core.hooksPath .githooks
```

## Deployment

Production runs as a **Docker Compose** stack (Flask image + MongoDB) or on **Kubernetes**
(`kubernetes/`: deployments, sealed secrets, cert-manager issuers, MetalLB, ingress-nginx). See
`docker/README.md` and `kubernetes/README.md`.

On `master`, `.github/workflows/build-deploy.yml` runs the tests, **builds the image on the
runner and pushes it to `ghcr.io/trencho/aqra-backend`**, then connects to the host over
OpenVPN + SSH and applies the manifests. The node pulls the image; **nothing is built there**.
It used to be: `docker compose build` on the node wrote to Docker's image store while kubelet
read containerd's, so the Deployment sat in `ErrImageNeverPull` and `/api/v1/*` returned 503.

Before touching that pipeline:

- **A merge to `master` dispatches a production deploy.** Know the cluster's state first.
- **The deploy can also be dispatched by hand** from the Actions tab (`workflow_dispatch`). That is
  the recovery path when a deploy failed for an infrastructure reason rather than a code one:
  re-running it needs no commit.
- **`.github/workflows/client.ovpn` must not be deleted.** Nothing in the tree references it:
  the `OPENVPN_CONFIG` secret holds its *path*. The workflow now fails fast and names the file
  if it goes missing, because the VPN action's own error masks the path as `***`.

MongoDB is pinned to **`mongo:4.4`**, deliberately not to a patch version and deliberately not
newer: this node's CPU has no AVX, which MongoDB 5.0+ requires, and the floating `4.4` tag is
what is already running: pinning tighter would roll the database on the next apply.

pymongo 4.18 supports MongoDB 4.4 as its **minimum** server version. A pymongo release that raises
that floor cuts the Kubernetes deployment off its database, so review pymongo bumps against the
running Mongo version before merging them.

## Data sources and legal

- Air-quality readings come from [pulse.eco](https://pulse.eco), a crowdsourced network that does
  not guarantee their correctness.
- Weather and air-pollution forecasts come from [OpenWeather](https://openweathermap.org) under the
  ODbL. Show "Weather data © OpenWeather" wherever you display them.
- Forecasts are statistical estimates. Do not use them for health or safety decisions.
- The code is Apache-2.0 ([`LICENSE`](LICENSE)); data attributions are in [`NOTICE`](NOTICE).
- What the API records about visitors, and where, is in
  [`docs/legal/data-processing-facts.md`](docs/legal/data-processing-facts.md). The API sets no
  cookies.
- Report vulnerabilities as described in [`SECURITY.md`](SECURITY.md).

## Roadmap

[`ROADMAP.md`](ROADMAP.md) holds the full list with a first step for each item. In order:

1. Move training and forecasting out of the web process into an RQ worker with its own Deployment.
2. Version model artefacts per training run behind an atomic `current` pointer.
3. Extract a forecast service with UTC time, an in-process model cache and one data read per sensor.
4. Make MongoDB the single source of truth, with the processed CSVs as a rebuildable cache.
5. Key the rate limiter on the real client IP behind two proxies, and add security headers.
6. Publish privacy, terms, cookie, data-source and disclaimer pages (pending FEIT review).

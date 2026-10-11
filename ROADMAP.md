# Roadmap

Development work this repository and its frontend would accept a pull request for. Each item names
its first step. Items are ordered by value within each section. Finished work moves to git history.

## Backend: architecture

1. **One city/sensor lookup for the blueprints.** Seven copies of the same two 404 guards, each
   with its own wording, drift as resources are added. First step: a helper in
   `api/blueprints/__init__.py` that returns the city and sensor or the 404, taking the wording.
2. **One OpenWeather fetcher.** `fetch_open_weather_data` and `fetch_pollution_data` repeat the
   request, status check, quota lock and save. First step: extract the shared path behind the
   existing tests in `tests/test_weather_data.py`.
3. **Split `process_data`.** About 85 lines that read, merge, impute, compute AQI and write. First
   step: extract the imputation and AQI steps under the existing processing tests.

## Backend: security and privacy

1. **Access-log minimisation.** The pod's nginx logs client IP and user agent for every request.
   First step: decide whether the user agent is needed, and drop it from `log_format` if not.

## Frontend (aqra-frontend)

1. **Model-accuracy panel** from the evaluation endpoints, and **prediction/error plots** via
   `<img>` against the plots routes.
2. **Weather history** next to pollution history: the backend already serves both on one route.
3. **Country selection.** Cities outside North Macedonia are fetched and drawn off-screen.
4. **Sensor health and an empty state**, so an offline sensor and a missing forecast stop looking
   like app failures.
5. **Deep-linkable map state**, then native share of a map view.
6. **Distinguish "API down" from "no data"** with one readiness probe at boot.
7. **Time-range control on Statistics.** The history routes already take `start_time` and
   `end_time` (one week at most).

### Items that change the privacy position

These need consent and policy updates **before** they ship, not after:

- **Nearest sensor to the visitor** (browser or Capacitor geolocation). Location is personal data;
  ask at the moment of use, send coordinates only to this API, and say so in the privacy policy.
- **Last-known readings offline** (`@capacitor/preferences`). Device storage of non-essential data;
  the cookie and storage policy must list it.
- **Local notifications on a pollutant threshold.** Needs the notification permission and a stored
  threshold.

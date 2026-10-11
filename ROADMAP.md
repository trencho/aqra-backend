# Roadmap

Development work this repository and its frontend would accept a pull request for. Each item names
its first step. Items are ordered by value within each section. Finished work moves to git history.

## Backend: architecture

1. **Versioned model artefacts.** One mutable folder per pollutant holds the model, the scaler and
   the manifest; the manifest is written before the model, and a stale `.mdl` from a previous winner
   can be loaded instead of the current one. First step: write each training run to its own
   directory and publish it by swapping a `current` pointer atomically after verification.
2. **A forecast service.** The forecast blueprint reads files and falls back to the database inline,
   the recursive forecast mixes naive local time with UTC, and its cache never hits because it is
   keyed on a model object. First step: extract `ForecastService` with an injected clock and key the
   model cache on the registry version.
3. **MongoDB as the single source of truth.** Readings are stored three times (Mongo, raw CSV,
   processed CSV) and rebuilt from Mongo on every boot. First step: give `weather` and `pollution` a
   unique `(sensorId, time)` index with upsert writes, then rebuild the CSVs from Mongo as a cache.
4. **Retire dead code.** `feature_imputation.py`, `train_city_sensors`, four regression models that
   are never enabled, and helpers with no callers. First step: delete them with their tests, keeping
   the test that pins `get_dummies` without `drop_first`.

## Backend: security and privacy

1. **Access-log minimisation.** The pod's nginx logs client IP and user agent for every request.
   First step: decide whether the user agent is needed, and drop it from `log_format` if not.

## Frontend (aqra-frontend)

1. **Legal pages and visible attribution.** Privacy, terms, cookies, data sources, disclaimer and
   accessibility pages, with "Weather data © OpenWeather" and the OpenStreetMap credit in the footer
   (OpenWeather requires visible attribution). First step: lazy-loaded routes outside the tab bar.
2. **Self-host the Roboto font.** Google Fonts sends every visitor's IP to Google. First step:
   `@fontsource/roboto` and delete the stylesheet link in `index.html`.
3. **Model-accuracy panel** from the evaluation endpoints, and **prediction/error plots** via
   `<img>` against the plots routes.
4. **Weather history** next to pollution history: the backend already serves both on one route.
5. **Country selection.** Cities outside North Macedonia are fetched and drawn off-screen.
6. **Sensor health, a heatmap legend and an empty state**, so an offline sensor, an unreadable
   colour and a missing forecast stop looking like app failures.
7. **Deep-linkable map state**, then native share of a map view.
8. **Distinguish "API down" from "no data"** with one readiness probe at boot.
9. **Time-range control on Statistics.** The history routes already take `start_time` and
   `end_time` (one week at most).

### Items that change the privacy position

These need consent and policy updates **before** they ship, not after:

- **Nearest sensor to the visitor** (browser or Capacitor geolocation). Location is personal data;
  ask at the moment of use, send coordinates only to this API, and say so in the privacy policy.
- **Last-known readings offline** (`@capacitor/preferences`). Device storage of non-essential data;
  the cookie and storage policy must list it.
- **Local notifications on a pollutant threshold.** Needs the notification permission and a stored
  threshold.

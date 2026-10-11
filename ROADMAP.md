# Roadmap

Development work this repository and its frontend would accept a pull request for. Each item names
its first step. Items are ordered by value within each section. Finished work moves to git history.

## Backend: architecture

1. **Retire dead code.** `feature_imputation.py`, four regression models that
   are never enabled, and helpers with no callers. First step: delete them with their tests, keeping
   the test that pins `get_dummies` without `drop_first`.

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

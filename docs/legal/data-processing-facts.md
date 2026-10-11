# Data-processing facts: aqra-backend

The privacy, cookie and terms pages in [aqra-frontend](https://github.com/trencho/aqra-frontend)
cite this sheet. It records what the code and the deployment manifests in this repository do,
with the file that shows each fact. It is **not a policy** and not legal advice. The pages built
from it stay unpublished until FEIT/UKIM, the controller, has reviewed them.

When a change here alters any line below, update this sheet in the same pull request.

## Who

- **Controller:** Faculty of Electrical Engineering and Information Technologies (FEIT), Ss. Cyril
  and Methodius University in Skopje (UKIM), which hosts `aqra.feit.ukim.edu.mk`.
- **Privacy contact:** `[FEIT DPO contact]`, a placeholder until FEIT names one.
- **Jurisdiction:** North Macedonia's Law on Personal Data Protection (ZZLP), which follows the
  GDPR. Visitors in the EU bring the GDPR into scope as well.

## What the API records about a visitor

The API has no accounts, no forms and no write endpoints, and **sets no cookies**: nothing in
`src/` calls `set_cookie` or uses a Flask session.

| Data | Where | Why | Kept for | Evidence |
|---|---|---|---|---|
| IP address, time, request line, status, bytes, referrer, user agent, `X-Forwarded-For` | the pod's nginx access log, written to the container's stdout | operating and securing the service | the hosting platform's container-log rotation, which this repository does not set | `docker/nginx.conf` (`log_format main`, `access_log /dev/stdout`) |
| Client IP, timestamp, request | the ingress-nginx controller's access log | the same | set by the cluster, not by this repository | `kubernetes/ingress/ingress-nginx-values.yml` (defaults) |
| IP address as a rate-limit key, with a request count | Redis, in memory only (`--save ""`, `--appendonly no`, `emptyDir`) | preventing abuse; at most 60 requests a minute and 1000 an hour per client | until the limit window expires, at most **1 hour**, and lost on restart | `src/api/config/limiter.py`, `definitions.py` (`RATE_LIMIT_DEFAULTS`), `kubernetes/deployment/redis-deployment.yml` |
| Coordinates in the URL of the coordinate endpoints | the access logs above | answering "nearest sensor/city" | as the access logs | `src/api/blueprints/*/` routes under `/coordinates/` |

The application's own log (`${VOLUME_PATH}/logs/app.log`, rotated at midnight, **5 previous days
kept**, `src/api/config/logger.py`) records job outcomes and errors. It does not log client IPs.

Prometheus metrics (`/metrics`, `src/api/config/metrics.py`) count requests per endpoint with no
client labels. The ingress routes only `/api`, so `/metrics` is not reachable from the internet
(`kubernetes/ingress/flask-ingress.yml`).

## Who else receives data

No visitor data leaves the service. The service itself contacts:

| Party | What is sent | Evidence |
|---|---|---|
| pulse.eco | requests for countries, cities and sensors; no visitor data | `src/preparation/location_data.py` |
| OpenWeather | each **sensor's** coordinates and the API key; no visitor data | `src/preparation/weather_data.py` |
| GitHub | every 15 days, an archive of the sensor and weather data to the **private** repository `air-quality-data-dump`; logs are not included | `src/api/config/schedule.py` (`dump_data`), `src/api/config/git.py` |

Swagger UI at `/api/v1/apidocs/` loads its scripts and styles from this host only. Its stock
template pulled Google Fonts; `src/api/config/templates/flasgger/head.html` removes that.

## Security measures that a policy may describe

- TLS at the ingress, with certificates from Let's Encrypt via cert-manager
  (`kubernetes/ingress/flask-ingress.yml`).
- `Content-Security-Policy`, `X-Content-Type-Options`, `Referrer-Policy` and `X-Frame-Options` on
  every response (`docker/nginx.conf`).
- The rate limiter keys on the real client IP behind both proxies (`TRUSTED_PROXY_HOPS=2` in
  `kubernetes/config-map/flask-config.yml`).
- The container runs as an unprivileged user (`docker/Dockerfile`, `securityContext` in
  `kubernetes/deployment/flask-deployment.yml`).

## Open points for the controller

- Confirm the **legal basis** for the access logs and the rate-limit key. Legitimate interest in
  security and abuse prevention is the usual one; the controller decides.
- Set and state a **retention period** for container and ingress logs on the cluster. None is set
  in this repository, so no policy should quote one yet.
- Decide whether the **user agent** needs to be logged at all; dropping it from `log_format` is a
  one-line change.
- Confirm the reuse terms for **pulse.eco** data; see `NOTICE`.
- Making `air-quality-data-dump` public would redistribute OpenWeather-derived data and trigger the
  ODbL's share-alike clause.

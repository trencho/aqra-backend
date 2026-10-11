#!/bin/bash
set -e

# One image, three roles, chosen by the first argument (default: api).
#   api     nginx + gunicorn serving /api/v1 (development: the waitress dev server)
#   worker  enqueue the startup rebuild, then drain the job queues
#   cron    enqueue scheduled jobs (src/worker/cron.py)
ROLE="${1:-api}"
APP_PATH="/app/src/api/app.py"
# definitions.py lives at /app and the packages under /app/src. gunicorn finds the former only
# because it starts in /app; a worker started anywhere else could not import it.
export PYTHONPATH="/app:/app/src${PYTHONPATH:+:${PYTHONPATH}}"
# Highest priority first: an hourly forecast must not wait behind training jobs.
QUEUES=(forecast ingest maintenance train)

case "${ROLE}" in
  api)
    if [ "${APP_ENV}" = "development" ]; then
      echo "Running app in development mode!"
      exec python3 "${APP_PATH}"
    fi
    echo "Running app in production mode!"
    nginx && exec gunicorn -c "/app/docker/gunicorn.conf.py"
    ;;
  worker)
    python3 -m worker bootstrap
    exec rq worker --url "${QUEUE_REDIS_URL}" "${QUEUES[@]}"
    ;;
  cron)
    exec rq cron --url "${QUEUE_REDIS_URL}" worker.cron
    ;;
  *)
    echo "Unknown role '${ROLE}'; expected api, worker or cron." >&2
    exit 64
    ;;
esac

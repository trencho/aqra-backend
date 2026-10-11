"""The schedule, loaded by `rq cron worker.cron`.

RQ's cron evaluates these in UTC. The APScheduler jobs they replace ran in the container's local
time (Europe/Skopje), so a midnight job now runs at 01:00 or 02:00 local. Nothing here depends on
local midnight: OpenWeather's daily quota resets on UTC.

The schedule lives in the cron process, not in Redis, so restarting the queue loses no
schedule; and the old APScheduler job store (data/jobs.sqlite) and its nightly SQL dump are gone.
"""

from rq import cron

from worker import forecast, ingest, maintenance, training
from worker.queues import FORECAST, INGEST, JOB_TIMEOUTS, MAINTENANCE, TRAIN

SCHEDULE = [
    # (function, queue, cron expression)
    (ingest.fetch_locations, INGEST, "0 0 * * *"),
    (ingest.import_data, INGEST, "0 0 * * *"),
    (ingest.reset_api_counter, INGEST, "0 0 * * *"),
    (ingest.fetch_hourly_data, INGEST, "0 */2 * * *"),
    (training.schedule_training, TRAIN, "0 * * * *"),
    # Five minutes after the hour, so the forecast reads the readings the hourly fetch and
    # processing wrote rather than racing them.
    (forecast.predict_locations, FORECAST, "5 * * * *"),
    (maintenance.dump_data, MAINTENANCE, "0 0 */15 * *"),
]

for function, queue, expression in SCHEDULE:
    cron.register(
        function,
        queue,
        cron=expression,
        job_timeout=JOB_TIMEOUTS[queue],
        name=f"{function.__module__}.{function.__name__}",
    )

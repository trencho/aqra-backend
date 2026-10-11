"""Queues, the connection they live on, and the one way jobs are enqueued.

The queue Redis is not the cache Redis. The cache runs `allkeys-lru` with no persistence, and
an evicted or restarted queue is a job that silently never runs, so `QUEUE_REDIS_URL` points at
an instance configured with `noeviction` and an append-only file.
"""

from logging import getLogger
from re import sub

from redis import Redis
from rq import Queue
from rq.exceptions import DuplicateJobError

from settings import get_settings

logger = getLogger(__name__)

# Listed in the order a worker should drain them: a forecast is due every hour and must not
# wait behind a few hundred training jobs.
FORECAST = "forecast"
INGEST = "ingest"
MAINTENANCE = "maintenance"
TRAIN = "train"
QUEUES = [FORECAST, INGEST, MAINTENANCE, TRAIN]

# Seconds. RQ's default job timeout is 180, which no training run here fits in.
JOB_TIMEOUTS = {
    FORECAST: 60 * 60,
    INGEST: 2 * 60 * 60,
    MAINTENANCE: 60 * 60,
    TRAIN: 2 * 60 * 60,
}
# A finished job's id stays reserved for this long, which is what turns `unique=True` into
# "at most one per window". Short, so the next scheduled run is never refused.
RESULT_TTL = 60
FAILURE_TTL = 7 * 24 * 60 * 60


def connection() -> Redis:
    url = get_settings().queue_redis_url
    if url is None:
        raise RuntimeError("QUEUE_REDIS_URL is not set; the worker has no queue to use")
    return Redis.from_url(url)


def job_id(*parts: str) -> str:
    """A stable RQ job id from free-form parts.

    RQ accepts letters, digits, `_` and `-` only, and city names and sensor ids are upstream
    data, so anything else becomes `_`.
    """
    return "-".join(sub(r"[^A-Za-z0-9_]", "_", str(part)) for part in parts)


def enqueue_unique(queue: Queue, func, *args, job_id: str, **kwargs):
    """Enqueue `func` unless a job with the same id is queued, running or just finished.

    This replaces the `.lock` files training used to write next to each model. Those were a
    check-then-create race, and a process killed mid-run left its lock behind until a
    bi-monthly job deleted it, freezing that pollutant's model in the meantime. A duplicate
    here is refused atomically by Redis and the reservation expires with the job.
    """
    try:
        return queue.enqueue(
            func,
            *args,
            job_id=job_id,
            unique=True,
            job_timeout=JOB_TIMEOUTS.get(queue.name, JOB_TIMEOUTS[MAINTENANCE]),
            result_ttl=RESULT_TTL,
            failure_ttl=FAILURE_TTL,
            **kwargs,
        )
    except DuplicateJobError:
        logger.info("%s is already queued or running; not enqueued again", job_id)
        return None

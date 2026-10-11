"""Liveness for the worker container: exit 0 if this host's RQ worker has a fresh heartbeat.

An exec probe runs inside the pod, which cannot see a dead pod from outside. That limit is
acceptable here: the question is whether the worker process in THIS container is still
heartbeating into the queue, which is exactly what the worker registry records.
"""

from datetime import UTC, datetime, timedelta
from socket import gethostname
from sys import exit

from rq import Worker

from worker.queues import connection

STALE_AFTER = timedelta(minutes=3)


def worker_is_alive(workers, hostname: str, now: datetime) -> bool:
    return any(
        worker.hostname == hostname
        and worker.last_heartbeat is not None
        and now - worker.last_heartbeat.replace(tzinfo=UTC) < STALE_AFTER
        for worker in workers
    )


if __name__ == "__main__":
    workers = Worker.all(connection=connection())
    exit(0 if worker_is_alive(workers, gethostname(), datetime.now(UTC)) else 1)

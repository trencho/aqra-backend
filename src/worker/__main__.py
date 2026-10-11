"""Enqueue a job by hand: `python -m worker <job>`, for example `python -m worker bootstrap`.

The worker runs it like a scheduled one. `--now` runs it in this process instead, which is the
way to drive a job without a queue at all.
"""

from argparse import ArgumentParser

from rq import Queue

from worker import forecast, ingest, maintenance, training
from worker.queues import (
    FORECAST,
    INGEST,
    MAINTENANCE,
    TRAIN,
    connection,
    enqueue_unique,
    job_id,
)

JOBS = {
    "bootstrap": (ingest.bootstrap, INGEST),
    "fetch_locations": (ingest.fetch_locations, INGEST),
    "fetch_hourly_data": (ingest.fetch_hourly_data, INGEST),
    "import_data": (ingest.import_data, INGEST),
    "schedule_training": (training.schedule_training, TRAIN),
    "predict_locations": (forecast.predict_locations, FORECAST),
    "dump_data": (maintenance.dump_data, MAINTENANCE),
}


def main(argv=None) -> None:
    parser = ArgumentParser(prog="python -m worker")
    parser.add_argument("job", choices=sorted(JOBS))
    parser.add_argument("--now", action="store_true", help="run here, not on a worker")
    args = parser.parse_args(argv)

    function, queue_name = JOBS[args.job]
    if args.now:
        function()
        return
    enqueue_unique(
        Queue(queue_name, connection=connection()),
        function,
        job_id=job_id("manual", args.job),
    )


if __name__ == "__main__":
    main()

"""Training jobs: one fan-out that enqueues a job per (city, sensor, pollutant)."""

from logging import getLogger

from rq import Queue

from caching import cache
from definitions import POLLUTANTS
from modeling import train_regression_model
from preparation import check_city, check_sensor, read_cities, read_sensors
from utils import BatchOutcome, BatchTally, track_time

from .context import in_app_context
from .queues import TRAIN, connection, enqueue_unique, job_id

logger = getLogger(__name__)


@in_app_context
@track_time
def schedule_training() -> None:
    """Enqueue one training job per model, each under a unique id.

    The old job trained every model in one loop. One job per model lets the worker drain
    forecasts between them, makes a failure cost one model rather than the rest of the loop,
    and lets RQ refuse a second copy of a model that is still training.
    """
    queue = Queue(TRAIN, connection=connection())
    with BatchTally(logger, "schedule_training", "model") as tally:
        for city in cache.get("cities") or read_cities():
            for sensor in read_sensors(city["cityName"]):
                for pollutant in tally.track(POLLUTANTS):
                    if (
                        enqueue_unique(
                            queue,
                            train_model,
                            city["cityName"],
                            sensor["sensorId"],
                            pollutant,
                            job_id=job_id(
                                "train", city["cityName"], sensor["sensorId"], pollutant
                            ),
                        )
                        is None
                    ):
                        tally.record(BatchOutcome.SKIPPED)


@in_app_context
def train_model(city_name: str, sensor_id: str, pollutant: str) -> str:
    """Train one model. Takes names rather than dicts so the job payload stays small."""
    if (city := check_city(city_name)) is None or (
        sensor := check_sensor(city_name, sensor_id)
    ) is None:
        logger.warning("%s - %s is no longer in the catalogue", city_name, sensor_id)
        return BatchOutcome.SKIPPED.value
    outcome = train_regression_model(city, sensor, pollutant)
    if outcome is BatchOutcome.FAILED:
        # Raise so RQ records the job as failed and keeps it for FAILURE_TTL, where
        # `rq info` and the failed-job registry show it, instead of a green job.
        raise RuntimeError(
            f"training failed for {city_name} - {sensor_id} - {pollutant}"
        )
    return outcome.value

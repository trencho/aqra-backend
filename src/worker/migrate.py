"""One-off: make the database hold every cached reading once, then index it.

    python -m worker.migrate --dry-run   # report, change nothing
    python -m worker.migrate             # push, de-duplicate, create the unique index

The worker's startup `bootstrap` already pushes each sensor's local-only readings before it
rebuilds the cache, so this is not needed for correctness. It is the step that can be checked by
hand: it prints before and after counts, and it removes the duplicate readings older code could
insert, which is what lets `ensure_indexes` create the unique (sensorId, time) index. Run it once
when the cluster is reachable again; running it twice changes nothing the second time.
"""

from argparse import ArgumentParser
from logging import INFO, basicConfig, getLogger

from definitions import COLLECTIONS, READING_KEYS
from preparation import read_cities, read_sensors
from repository import get_repository
from utils import BatchTally

from .ingest import push_local_readings

logger = getLogger(__name__)


def stored_count(collection: str) -> int:
    return get_repository().count(collection)


def migrate(dry_run: bool = False) -> dict:
    """Returns {collection: rows pushed (or that would be)} plus the duplicates removed."""
    summary = {collection: 0 for collection in COLLECTIONS}
    before = {collection: stored_count(collection) for collection in COLLECTIONS}

    with BatchTally(logger, "migrate", "sensor") as tally:
        for city in read_cities():
            for sensor in tally.track(read_sensors(city["cityName"])):
                try:
                    for collection in COLLECTIONS:
                        summary[collection] += push_local_readings(
                            collection, city["cityName"], sensor["sensorId"], dry_run
                        )
                except Exception:
                    tally.failure(
                        f"Could not migrate {city['cityName']} - {sensor['sensorId']}"
                    )

    if dry_run:
        for collection in COLLECTIONS:
            logger.info(
                "%s: %d stored, %d cached readings missing from the database",
                collection,
                before[collection],
                summary[collection],
            )
        return summary

    for collection in COLLECTIONS:
        removed = get_repository().delete_duplicates(collection, READING_KEYS)
        summary[f"{collection}_duplicates_removed"] = removed
        logger.info(
            "%s: %d stored before, %d pushed, %d duplicates removed, %d stored after",
            collection,
            before[collection],
            summary[collection],
            removed,
            stored_count(collection),
        )
    get_repository().ensure_indexes()
    return summary


def main(argv=None) -> None:
    parser = ArgumentParser(prog="python -m worker.migrate")
    parser.add_argument("--dry-run", action="store_true", help="report only")
    args = parser.parse_args(argv)
    basicConfig(level=INFO)
    migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()

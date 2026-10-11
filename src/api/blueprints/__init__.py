from http import HTTPStatus
from logging import getLogger
from pathlib import Path

from flask import Response, jsonify
from pandas import DataFrame

from api.config.cache import cache
from definitions import CACHE_TIMEOUTS, DATA_PROCESSED_PATH
from processing import read_csv_in_chunks

logger = getLogger(__name__)


@cache.memoize(timeout=CACHE_TIMEOUTS["1h"])
def fetch_dataframe(
    data_path: Path, collection: str
) -> DataFrame | tuple[Response, int]:
    try:
        dataframe = read_csv_in_chunks(
            DATA_PROCESSED_PATH / data_path / f"{collection}.csv"
        )
    except (OSError, ValueError) as error:
        # OSError covers the file being absent or unreadable; ValueError covers pandas
        # refusing its contents, including the `concat` of an empty chunk list. Anything
        # outside those two is a defect in this service rather than missing data, and it
        # should reach the error handler instead of being answered with a 404.
        logger.warning(
            "Could not read the %s data for %s: %s", collection, data_path, error
        )
        dataframe = None

    if dataframe is not None:
        return dataframe

    # read_csv_in_chunks returns None for a file that parsed but held no usable rows. That
    # used to be signalled by raising a bare Exception into the handler above purely to
    # reach this return, which logged a manufactured traceback for the ordinary case of a
    # city having no history yet. It is a plain branch now, and it says which case it is.
    logger.info("No %s data available for %s", collection, data_path)
    return (
        jsonify(
            error_message=f"Cannot return data because the {collection} data is missing for that city and sensor."
        ),
        HTTPStatus.NOT_FOUND,
    )

from logging import getLogger

from definitions import (
    DATA_EXTERNAL_PATH,
    DATA_PROCESSED_PATH,
    DATA_RAW_PATH,
    LOG_PATH,
    MODELS_PATH,
    RESULTS_ERRORS_PATH,
    RESULTS_PREDICTIONS_PATH,
)
from settings import missing_required_variables

logger = getLogger(__name__)


SYSTEM_PATHS = [
    DATA_EXTERNAL_PATH,
    DATA_PROCESSED_PATH,
    DATA_RAW_PATH,
    LOG_PATH,
    MODELS_PATH,
    RESULTS_ERRORS_PATH,
    RESULTS_PREDICTIONS_PATH,
]


def init_environment_variables() -> None:
    if missing_envs := missing_required_variables():
        logger.error(f"Missing environment variables: {', '.join(missing_envs)}")
        raise SystemExit(1)


def init_system_paths() -> None:
    for system_path in SYSTEM_PATHS:
        system_path.mkdir(parents=True, exist_ok=True)

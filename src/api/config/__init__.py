from flask import Flask

from repository import get_repository
from settings import get_settings

from .blueprints import init_blueprints
from .cache import init_cache
from .converters import init_converters
from .cors import init_cors
from .environment import init_data, init_environment_variables, init_system_paths
from .garbage_collection import init_gc
from .health import init_healthcheck
from .limiter import init_limiter
from .logger import init_logger
from .metrics import init_metrics
from .schedule import init_scheduler
from .swagger import init_swagger


def create_app() -> Flask:
    init_logger()
    init_gc()
    init_system_paths()

    app = Flask(__name__)

    settings = get_settings()
    if settings.is_production:
        init_environment_variables()
        get_repository().ensure_indexes()
        init_scheduler()

    init_converters(app)
    init_blueprints(app)

    init_cache(app)
    init_cors(app)
    init_healthcheck(app)
    init_limiter(app)
    init_metrics(app)
    init_swagger(app)

    if not settings.skip_data_fetch:
        init_data()

    return app

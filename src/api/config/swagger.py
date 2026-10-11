from logging import getLogger

from flasgger import Swagger
from flask import Flask

from definitions import URL_PREFIX

logger = getLogger(__name__)

CONFIG = {
    "favicon": f"{URL_PREFIX}/favicon.ico/",
    "openapi": "3.0.3",
    "specs": [
        {
            "endpoint": "apispec_1",
            "route": f"{URL_PREFIX}/apispec_1.json",
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": f"{URL_PREFIX}/flasgger_static",
    "specs_route": f"{URL_PREFIX}/apidocs/",
    "title": "AQRA",
    # flasgger renders this straight into the page script as `let auth_config = ...;`. Left
    # unset it prints Python's `None`, which throws a ReferenceError in every browser.
    "auth": {},
}

TEMPLATE = {
    "info": {
        "title": "AQRA",
        "description": "Air-quality readings, history and forecasts for the pulse.eco sensor "
        "network. Weather data © OpenWeather. Forecasts are statistical estimates and must "
        "not be used for health or safety decisions.",
        "termsOfService": "https://aqra.feit.ukim.edu.mk/terms",
        "contact": {
            "name": "Faculty of Electrical Engineering and Information Technologies, UKIM",
            "url": "https://aqra.feit.ukim.edu.mk/privacy",
        },
        "license": {
            "name": "Apache-2.0",
            "url": "https://www.apache.org/licenses/LICENSE-2.0",
        },
    },
}

swagger = Swagger(config=CONFIG, template=TEMPLATE, merge=True)


def init_swagger(app: Flask) -> None:
    swagger.init_app(app)
    logger.info("Swagger configured successfully")

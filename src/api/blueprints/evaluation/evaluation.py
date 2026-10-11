from http import HTTPStatus
from typing import Optional

from flasgger import swag_from
from flask import Blueprint, Response, jsonify
from pandas import isna

from api.config.cache import cache
from api.config.converters import PollutantType
from definitions import CACHE_TIMEOUTS, POLLUTANTS
from models.registry import ModelKey, current_dir, read_manifest
from preparation import check_city, check_sensor

evaluation_blueprint = Blueprint("evaluation", __name__)


def _read_best_model_evaluation(
    city_name: str, sensor_id: str, pollutant: str
) -> Optional[dict]:
    # The forecast is served by the registry's current run; report that run's model and the
    # held-out errors its manifest recorded when it was published.
    run_dir = current_dir(ModelKey(city_name, sensor_id, pollutant))
    if run_dir is None or (manifest := read_manifest(run_dir)) is None:
        return None
    if not (model_name := manifest.get("model", {}).get("class")):
        return None
    return {
        "model": model_name,
        "metrics": {
            name: (None if isna(value) else float(value))
            for name, value in (manifest.get("metrics") or {}).items()
        },
    }


@evaluation_blueprint.get(
    "/cities/<string:city_name>/sensors/<string:sensor_id>/evaluation/",
    endpoint="city_sensor",
)
@cache.memoize(timeout=CACHE_TIMEOUTS["1h"])
@swag_from(
    "evaluation_city_sensor.yml", endpoint="evaluation.city_sensor", methods=["GET"]
)
def fetch_sensor_evaluation(
    city_name: str, sensor_id: str
) -> Response | tuple[Response, int]:
    if check_city(city_name) is None:
        return (
            jsonify(
                error_message="Cannot return forecast quality because the city is not found or is invalid."
            ),
            HTTPStatus.NOT_FOUND,
        )

    if check_sensor(city_name, sensor_id) is None:
        return (
            jsonify(
                error_message="Cannot return forecast quality because the sensor is not found or is invalid."
            ),
            HTTPStatus.NOT_FOUND,
        )

    results = {}
    for pollutant in POLLUTANTS:
        if (
            evaluation := _read_best_model_evaluation(city_name, sensor_id, pollutant)
        ) is not None:
            results[pollutant] = evaluation

    return jsonify(results)


@evaluation_blueprint.get(
    "/cities/<string:city_name>/sensors/<string:sensor_id>/pollutants/"
    "<pollutant_type:pollutant>/evaluation/",
    endpoint="city_sensor_pollutant",
)
@cache.memoize(timeout=CACHE_TIMEOUTS["1h"])
@swag_from(
    "evaluation_city_sensor_pollutant.yml",
    endpoint="evaluation.city_sensor_pollutant",
    methods=["GET"],
)
def fetch_pollutant_evaluation(
    city_name: str, sensor_id: str, pollutant: PollutantType
) -> Response | tuple[Response, int]:
    if check_city(city_name) is None:
        return (
            jsonify(
                error_message="Cannot return forecast quality because the city is not found or is invalid."
            ),
            HTTPStatus.NOT_FOUND,
        )

    if check_sensor(city_name, sensor_id) is None:
        return (
            jsonify(
                error_message="Cannot return forecast quality because the sensor is not found or is invalid."
            ),
            HTTPStatus.NOT_FOUND,
        )

    if (
        evaluation := _read_best_model_evaluation(city_name, sensor_id, pollutant)
    ) is None:
        return (
            jsonify(
                error_message="Cannot return forecast quality because no trained model exists for that pollutant."
            ),
            HTTPStatus.NOT_FOUND,
        )

    return jsonify(evaluation)

"""The OpenWeather fetchers: success saves, failure locks the quota, nothing hits the network."""

import pytest

from preparation import weather_data

SENSOR = {"sensorId": "1000", "position": "41.99,21.43"}


class _Response:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code, self._payload, self.text = status_code, payload, text

    def json(self):
        return self._payload


@pytest.fixture
def fetch_env(tmp_path, monkeypatch):
    saved = []
    monkeypatch.setattr(weather_data, "DATA_PATH", tmp_path)
    monkeypatch.setattr(weather_data, "DATA_RAW_PATH", tmp_path / "raw")
    monkeypatch.setattr(weather_data, "sleep", lambda seconds: None)
    monkeypatch.setenv("OPEN_WEATHER_TOKEN", "test-token")
    monkeypatch.setattr(
        weather_data,
        "save_dataframe",
        lambda frame, collection, path, sensor_id: saved.append((collection, frame)),
    )
    return tmp_path, saved


def _respond(monkeypatch, response, calls=None):
    def get(url, params):
        if calls is not None:
            calls.append((url, params))
        return response

    monkeypatch.setattr(weather_data, "get", get)


def test_weather_is_flattened_and_saved(fetch_env, monkeypatch):
    _, saved = fetch_env
    calls = []
    hourly = [{"dt": 3600, "temp": 7.5, "weather": [{"icon": "01d"}]}]
    _respond(monkeypatch, _Response(200, {"hourly": hourly}), calls)

    assert weather_data.fetch_open_weather_data("skopje", SENSOR)
    collection, frame = saved[0]
    assert collection == "weather"
    assert {"dt", "temp", "weather_icon"} <= set(frame.columns)
    # The sensor's own coordinates go upstream, and the token comes from settings.
    assert "lat=41.99&lon=21.43" in calls[0][1]
    assert "appid=test-token" in calls[0][1]


def test_pollution_rows_merge_main_and_components(fetch_env, monkeypatch):
    _, saved = fetch_env
    payload = {"list": [{"dt": 3600, "main": {"aqi": 2}, "components": {"pm10": 12.0}}]}
    _respond(monkeypatch, _Response(200, payload))

    assert weather_data.fetch_pollution_data("skopje", SENSOR)
    collection, frame = saved[0]
    assert collection == "pollution"
    assert frame.to_dict("records") == [{"time": 3600, "aqi": 2, "pm10": 12.0}]


@pytest.mark.parametrize(
    "fetch", [weather_data.fetch_open_weather_data, weather_data.fetch_pollution_data]
)
def test_an_upstream_error_locks_the_quota_and_reports_failure(
    fetch_env, monkeypatch, fetch
):
    data_path, saved = fetch_env
    _respond(monkeypatch, _Response(429, text="quota exceeded"))

    assert weather_data.api_is_available()
    assert fetch("skopje", SENSOR) is False
    assert not weather_data.api_is_available()  # every later fetch now skips
    assert saved == []


def test_fetch_weather_data_runs_both_even_when_the_first_fails(fetch_env, monkeypatch):
    calls = []
    monkeypatch.setattr(
        weather_data,
        "fetch_open_weather_data",
        lambda city, sensor: calls.append("weather") or False,
    )
    monkeypatch.setattr(
        weather_data,
        "fetch_pollution_data",
        lambda city, sensor: calls.append("pollution") or True,
    )
    assert weather_data.fetch_weather_data("skopje", SENSOR) is False
    assert calls == ["weather", "pollution"]

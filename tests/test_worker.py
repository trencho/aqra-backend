"""The RQ worker: ids, de-duplication, the training fan-out, the schedule, and liveness.

Hermetic. Queues run on FakeRedis (with Lua, which RQ's `unique=True` needs), and the job bodies
that would reach the network are stubbed. Nothing here starts a worker process.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from croniter import croniter
from fakeredis import FakeStrictRedis
from rq import Queue
from rq.cron import CronScheduler

from definitions import POLLUTANTS
from utils import BatchOutcome
from worker import health, ingest, queues, training
from worker.queues import enqueue_unique, job_id


@pytest.fixture
def redis(monkeypatch):
    server = FakeStrictRedis()
    monkeypatch.setattr(queues, "connection", lambda: server)
    monkeypatch.setattr(training, "connection", lambda: server)
    return server


def test_job_id_keeps_only_what_rq_accepts():
    # RQ refuses anything but letters, digits, `_` and `-`; city names are upstream data.
    assert job_id("train", "skopje", "sensor:1/2", "pm2.5") == (
        "train-skopje-sensor_1_2-pm2_5"
    )


def test_a_second_copy_of_a_queued_job_is_refused(redis):
    queue = Queue("train", connection=redis)
    first = enqueue_unique(queue, ingest.reset_api_counter, job_id="train-a")
    second = enqueue_unique(queue, ingest.reset_api_counter, job_id="train-a")
    assert first is not None
    assert second is None
    assert len(queue) == 1


def test_enqueue_sets_the_queue_timeout_not_rqs_180_seconds(redis):
    job = enqueue_unique(
        Queue("train", connection=redis), ingest.reset_api_counter, job_id="t"
    )
    assert job.timeout == queues.JOB_TIMEOUTS[queues.TRAIN]
    assert job.result_ttl == queues.RESULT_TTL


def test_schedule_training_enqueues_one_job_per_model(redis, monkeypatch):
    cities = [{"cityName": "skopje"}, {"cityName": "bitola"}]
    monkeypatch.setattr(training.cache, "get", lambda key: cities, raising=False)
    monkeypatch.setattr(
        training, "read_sensors", lambda city: [{"sensorId": f"{city}-1"}]
    )

    training.schedule_training()
    training.schedule_training()  # the second run finds every job already queued

    queue = Queue(queues.TRAIN, connection=redis)
    assert len(queue) == len(cities) * len(POLLUTANTS)
    assert "train-skopje-skopje_1-pm10" in queue.job_ids


def test_train_model_raises_on_failure_so_rq_records_it(monkeypatch):
    monkeypatch.setattr(training, "check_city", lambda name: {"cityName": name})
    monkeypatch.setattr(training, "check_sensor", lambda c, s: {"sensorId": s})
    monkeypatch.setattr(
        training, "train_regression_model", lambda *a: BatchOutcome.FAILED
    )
    with pytest.raises(RuntimeError):
        training.train_model("skopje", "1000", "pm10")

    monkeypatch.setattr(
        training, "train_regression_model", lambda *a: BatchOutcome.DONE
    )
    assert training.train_model("skopje", "1000", "pm10") == "done"


def test_train_model_skips_a_sensor_that_left_the_catalogue(monkeypatch):
    monkeypatch.setattr(training, "check_city", lambda name: {"cityName": name})
    monkeypatch.setattr(training, "check_sensor", lambda c, s: None)
    assert training.train_model("skopje", "gone", "pm10") == "skipped"


def test_an_inline_queue_runs_a_real_job(tmp_path, monkeypatch):
    lock = tmp_path / "open_weather.lock"
    lock.write_text("")
    monkeypatch.setattr(ingest, "DATA_PATH", tmp_path)
    queue = Queue("ingest", is_async=False, connection=FakeStrictRedis())
    queue.enqueue(ingest.reset_api_counter)
    assert not lock.exists()


def test_bootstrap_honours_skip_data_fetch(monkeypatch):
    calls = []
    monkeypatch.setenv("SKIP_DATA_FETCH", "1")
    monkeypatch.setattr(ingest, "fetch_locations", lambda: calls.append("locations"))
    monkeypatch.setattr(ingest, "fetch_db_data", lambda: calls.append("db"))
    ingest.bootstrap()
    assert calls == []

    monkeypatch.setenv("SKIP_DATA_FETCH", "0")
    ingest.bootstrap()
    assert calls == ["locations", "db"]


def test_the_cron_config_registers_every_job_once_with_a_valid_expression():
    from worker.cron import SCHEDULE

    for _ in range(2):  # loading twice must not double the schedule
        scheduler = CronScheduler(connection=FakeStrictRedis())
        scheduler.load_config_from_file("worker.cron")
        assert len(scheduler.get_jobs()) == len(SCHEDULE)

    for function, queue, expression in SCHEDULE:
        assert croniter.is_valid(expression), expression
        assert queue in queues.QUEUES


def test_health_requires_a_recent_heartbeat_from_this_host():
    now = datetime.now(UTC)

    def worker(hostname, seconds_ago):
        return SimpleNamespace(
            hostname=hostname,
            last_heartbeat=(now - timedelta(seconds=seconds_ago)).replace(tzinfo=None),
        )

    assert health.worker_is_alive([worker("pod-a", 10)], "pod-a", now)
    assert not health.worker_is_alive([worker("pod-a", 600)], "pod-a", now)
    assert not health.worker_is_alive([worker("pod-b", 10)], "pod-a", now)


def test_predict_locations_counts_a_failed_sensor_and_carries_on(monkeypatch, caplog):
    from worker import forecast

    published = []

    def publish(city, sensor):
        if sensor["sensorId"] == "bad":
            raise RuntimeError("no model")
        published.append(sensor["sensorId"])

    monkeypatch.setattr(forecast.cache, "get", lambda key: [{"cityName": "skopje"}])
    monkeypatch.setattr(
        forecast,
        "read_sensors",
        lambda city: [{"sensorId": "bad"}, {"sensorId": "good"}],
    )
    monkeypatch.setattr(forecast, "publish_forecast", publish)

    with caplog.at_level("INFO", logger=forecast.__name__):
        forecast.predict_locations()

    assert published == ["good"]
    assert any("1 done, 1 failed" in record.message for record in caplog.records)


def test_the_cli_runs_a_job_in_process_with_now(monkeypatch):
    import worker.__main__ as cli

    ran = []
    monkeypatch.setitem(cli.JOBS, "bootstrap", (lambda: ran.append("now"), "ingest"))
    cli.main(["bootstrap", "--now"])
    assert ran == ["now"]


def test_the_cli_enqueues_under_a_manual_id(redis, monkeypatch):
    import worker.__main__ as cli

    monkeypatch.setattr(cli, "connection", lambda: redis)
    cli.main(["fetch_locations"])
    assert Queue(queues.INGEST, connection=redis).job_ids == ["manual-fetch_locations"]

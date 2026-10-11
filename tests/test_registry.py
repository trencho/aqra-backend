"""The directory registry: atomic publish, verification before publish, retention."""

from models import registry


def _run(key, payload="ok"):
    run_dir = registry.new_run_dir(key)
    (run_dir / registry.MANIFEST).write_text(
        '{"schema": 0, "created": "2026-10-11T00:00:00+00:00"}'
    )
    (run_dir / "payload").write_text(payload)
    return run_dir


def test_nothing_is_current_until_something_is_published(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "MODELS_PATH", tmp_path)
    key = registry.ModelKey("skopje", "1000", "pm10")
    _run(key)
    assert registry.current_version(key) is None
    assert registry.current_dir(key) is None


def test_publish_moves_the_pointer_only_after_verification(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "MODELS_PATH", tmp_path)
    key = registry.ModelKey("skopje", "1000", "pm10")
    good = _run(key)
    assert registry.publish(key, good, verify=lambda run_dir: True)
    assert registry.current_dir(key) == good

    def explode(run_dir):
        raise ValueError("the parts disagree")

    bad = _run(key)
    assert not registry.publish(key, bad, verify=explode)
    assert not registry.publish(key, bad, verify=lambda run_dir: None)
    assert registry.current_dir(key) == good  # still serving the last good run


def test_a_pointer_to_a_missing_run_reads_as_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "MODELS_PATH", tmp_path)
    key = registry.ModelKey("skopje", "1000", "pm10")
    key.root().mkdir(parents=True)
    (key.root() / registry.CURRENT).write_text("20990101T000000Z-gone")
    assert registry.current_version(key) is None


def test_retention_keeps_the_newest_runs_and_never_the_current_one(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(registry, "MODELS_PATH", tmp_path)
    key = registry.ModelKey("skopje", "1000", "pm10")
    runs = [_run(key) for _ in range(registry.KEEP_RUNS + 2)]
    # Publish the OLDEST, so the current run falls outside the newest-N window.
    assert registry.publish(key, runs[0], verify=lambda run_dir: True)

    remaining = sorted(path.name for path in (key.root() / registry.RUNS).iterdir())
    assert runs[0].name in remaining
    assert set(path.name for path in runs[-registry.KEEP_RUNS :]) <= set(remaining)
    assert len(remaining) == registry.KEEP_RUNS + 1


def test_published_at_reads_the_manifest_not_the_file_time(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "MODELS_PATH", tmp_path)
    key = registry.ModelKey("skopje", "1000", "pm10")
    run_dir = _run(key)
    registry.publish(key, run_dir, verify=lambda run_dir: True)
    assert registry.published_at(key).isoformat() == "2026-10-11T00:00:00+00:00"

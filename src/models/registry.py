"""Versioned model artefacts on disk, published by swapping a pointer.

Each (city, sensor, pollutant) used to own one mutable folder holding the model, its scaler, its
feature list and its manifest. Training rewrote that folder in place while the forecast job
could be reading it, wrote the manifest before the model, and never deleted the previous
winner's `.mdl`, which the loader could then pick because it took `glob("*.mdl")[0]`.

Now every training run writes a new directory under `runs/`, and the run becomes current only
after it has been loaded back and verified, by replacing the `current` pointer file in one
`os.replace`. A reader sees either the old run or the new one, never half of either. Old runs
are pruned, keeping the last few as a rollback.

Layout, under MODELS_PATH/<city>/<sensor>/<pollutant>/::

    runs/<run_id>/<ModelClass>.mdl
    runs/<run_id>/scaler.pkl
    runs/<run_id>/selected_features.json
    runs/<run_id>/pipeline.json      written last; its presence marks a complete run
    current                           the run_id being served

MLflow was evaluated as the registry and rejected for now: its skinny package cannot host a
registry without a server or a deprecated file store, and the full package needs pyarrow, which
the production node's CPU cannot run (see docker/Dockerfile).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from json import JSONDecodeError, loads
from logging import getLogger
from os import replace
from pathlib import Path
from pickle import load as pickle_load
from shutil import rmtree
from uuid import uuid4

from definitions import MODELS_PATH, PIPELINE_SCHEMA

from . import make_model

logger = getLogger(__name__)

CURRENT = "current"
RUNS = "runs"
MANIFEST = "pipeline.json"
KEEP_RUNS = 3


@dataclass(frozen=True)
class ModelKey:
    city: str
    sensor: str
    pollutant: str

    def root(self) -> Path:
        return MODELS_PATH / self.city / self.sensor / self.pollutant


def new_run_dir(key: ModelKey) -> Path:
    """A fresh, empty directory for one training run, named so names sort by creation time.

    Microseconds, not seconds: two runs in the same second used to sort by their random suffix,
    so retention could delete the newer one.
    """
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}"
    run_dir = key.root() / RUNS / run_id
    run_dir.mkdir(parents=True)
    return run_dir


def current_version(key: ModelKey) -> str | None:
    pointer = key.root() / CURRENT
    try:
        run_id = pointer.read_text().strip()
    except OSError:
        return None
    if not run_id or not (key.root() / RUNS / run_id / MANIFEST).exists():
        return None
    return run_id


def current_dir(key: ModelKey) -> Path | None:
    run_id = current_version(key)
    return key.root() / RUNS / run_id if run_id else None


def read_manifest(run_dir: Path) -> dict | None:
    try:
        return loads((run_dir / MANIFEST).read_text())
    except (OSError, JSONDecodeError):
        return None


def published_at(key: ModelKey) -> datetime | None:
    """When the current run was trained, from its manifest rather than a file's mtime."""
    run_dir = current_dir(key)
    if run_dir is None or (manifest := read_manifest(run_dir)) is None:
        return None
    try:
        return datetime.fromisoformat(manifest["created"])
    except (KeyError, TypeError, ValueError):
        return None


def publish(key: ModelKey, run_dir: Path, verify) -> bool:
    """Make `run_dir` the current run if `verify(run_dir)` accepts it, then prune old runs.

    `verify` loads the run back and raises, or returns something falsy, if its parts disagree,
    so a run that would not serve is never published. Returns whether the pointer moved.
    """
    try:
        if not verify(run_dir):
            raise ValueError("verification rejected the run")
    except Exception:
        logger.exception("%s: not publishing %s", key, run_dir.name)
        return False

    pointer = key.root() / CURRENT
    staged = pointer.with_name(f".{CURRENT}.{uuid4().hex}")
    staged.write_text(run_dir.name)
    replace(staged, pointer)
    prune(key)
    return True


def prune(key: ModelKey, keep: int = KEEP_RUNS) -> None:
    """Delete all but the newest `keep` runs, never the current one."""
    runs_dir = key.root() / RUNS
    if not runs_dir.exists():
        return
    current = current_version(key)
    runs = sorted((path for path in runs_dir.iterdir() if path.is_dir()), reverse=True)
    for stale in runs[keep:]:
        if stale.name != current:
            rmtree(stale, ignore_errors=True)


class ModelArtifactMismatch(ValueError):
    """A model, its feature list and its scaler do not describe the same training run."""


def verify_pipeline(run_dir, manifest, model, model_features, scaler) -> None:
    """Refuse a set whose parts disagree, rather than serving mismatched columns."""
    if list(manifest.get("features", [])) != list(model_features):
        raise ModelArtifactMismatch(
            "the manifest's feature list and selected_features.json differ"
        )

    scaler_features = getattr(scaler, "feature_names_in_", None)
    if scaler_features is not None and len(scaler_features) != len(model_features):
        raise ModelArtifactMismatch(
            f"the scaler was fitted on {len(scaler_features)} features, "
            f"the model expects {len(model_features)}"
        )


@lru_cache(maxsize=64)
def load_run(run_dir: Path) -> tuple | None:
    """Load a run's model, features and scaler, or None if they cannot be served together.

    Cached in process by path. A run directory never changes after it is published, so a new
    model is a new path and the cache cannot serve a stale one. This replaces memoizing the
    loader in Flask-Caching, which pickled every model into Redis on each call.
    """
    manifest = read_manifest(run_dir)
    if manifest is None:
        logger.warning("%s: no readable %s; skipping", run_dir, MANIFEST)
        return None
    if manifest.get("schema") != PIPELINE_SCHEMA:
        logger.info(
            "%s: pipeline schema %s, expected %s; skipping until retrained",
            run_dir,
            manifest.get("schema"),
            PIPELINE_SCHEMA,
        )
        return None

    model_class = manifest.get("model", {}).get("class")
    scaler_path = run_dir / manifest.get("scaler", {}).get("file", "scaler.pkl")
    if not model_class or not (run_dir / f"{model_class}.mdl").exists():
        logger.warning("%s: the manifest names no model file that exists", run_dir)
        return None
    if not scaler_path.exists():
        logger.warning("%s: the scaler is missing", run_dir)
        return None

    model = make_model(model_class)
    model.load(run_dir)
    model_features = loads((run_dir / "selected_features.json").read_text())
    with open(scaler_path, "rb") as in_file:
        scaler = pickle_load(in_file)

    try:
        verify_pipeline(run_dir, manifest, model, model_features, scaler)
    except ModelArtifactMismatch as mismatch:
        logger.error("%s: %s", run_dir, mismatch)
        return None
    return model, model_features, scaler


def load_current(key: ModelKey) -> tuple | None:
    run_dir = current_dir(key)
    return load_run(run_dir) if run_dir is not None else None

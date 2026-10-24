"""Metadados e publicação de uma execução de treino, sem depender das imagens."""

import json
import os
import tempfile
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, ensure_ascii=False, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def record_id(record):
    return str(record.get("id") or Path(record["_path"]).stem)


def record_key(record):
    return f"{record.get('_shard') or '.'}/{record_id(record)}"


def compact_record(record):
    categories = Counter(
        str(box["category"])
        for box in record.get("boxes") or []
        if box.get("category")
    )
    sizes = sorted({
        (int(box["width"]), int(box["height"]))
        for box in record.get("boxes") or []
        if box.get("width", 0) > 0 and box.get("height", 0) > 0
    })
    return {
        "id": record_id(record),
        "_shard": record.get("_shard") or ".",
        "session": str(record["session"]) if record.get("session") is not None else None,
        "phash": str(record["phash"]) if record.get("phash") is not None else None,
        "action": record.get("action"),
        "action_kind": record.get("action_kind"),
        "state": record.get("state"),
        "outcome": record.get("outcome"),
        "frame_width": record.get("frame_width"),
        "frame_height": record.get("frame_height"),
        "categories": dict(categories),
        "box_sizes": sizes,
    }


def trained_sample_ids(dataset_root, cache_root=None):
    from dataset.training_files import trained_sample_ids as read_trained_ids

    return read_trained_ids(dataset_root, cache_root)


def summarize(records):
    records = list(records)
    result = {"frames": len(records)}
    for output, field in (
        ("actions", "action"),
        ("action_types", "action_kind"),
        ("states", "state"),
        ("outcomes", "outcome"),
    ):
        result[output] = dict(sorted(Counter(
            str(record.get(field)) if record.get(field) is not None else "null"
            for record in records
        ).items()))
    result["sample_types"] = dict(Counter(
        "negative" if record.get("action") in (None, "") else "action"
        for record in records
    ))
    resolutions = Counter()
    categories = Counter()
    for record in records:
        width, height = record.get("frame_width"), record.get("frame_height")
        resolution = f"{width}x{height}" if width and height else record.get("_shard", ".")
        resolutions[str(resolution)] += 1
        if "categories" in record:
            categories.update(record["categories"])
        else:
            categories.update(
                str(box["category"])
                for box in record.get("boxes") or []
                if box.get("category")
            )
    result["resolutions"] = dict(sorted(resolutions.items()))
    result["categories"] = dict(sorted(categories.items()))
    return result


class TrainingRun:
    def __init__(self, ia_root, args):
        self.root = Path(ia_root)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.id = f"{stamp}-{uuid.uuid4().hex[:8]}"
        self.path = self.root / "training_reports" / f"{self.id}.json"
        self.started = time.monotonic()
        self.data = {
            "run_id": self.id,
            "started_at_utc": utc_now(),
            "finished_at_utc": None,
            "duration_seconds": None,
            "trigger": "automatic" if getattr(args, "automatic", False) else "manual",
            "status": "running",
            "parameters": {
                key: str(value) if isinstance(value, Path) else value
                for key, value in vars(args).items()
            },
            "current_batch": {},
            "replay": {},
            "evaluation": {},
            "final_fit": {},
            "artifacts": {},
            "cleanup": {"status": "not_started"},
        }

    def save(self):
        self.data["duration_seconds"] = round(time.monotonic() - self.started, 3)
        atomic_json(self.path, self.data)

    def finish(self, status):
        self.data["status"] = status
        self.data["finished_at_utc"] = utc_now()
        if status == "success":
            self._compact_success()
        self.save()

    def _compact_success(self):
        if "training" in self.data:
            return
        evaluation = self.data.get("evaluation", {})
        validation = evaluation.get("validation", {})
        artifacts = self.data.get("artifacts", {})
        parameter_names = (
            "kind", "resolution", "trees", "seed", "jobs", "negatives",
            "split", "test_ratio", "outcome",
        )
        self.data = {
            key: self.data[key]
            for key in (
                "run_id", "started_at_utc", "finished_at_utc",
                "duration_seconds", "trigger", "status",
            )
            if key in self.data
        } | {
            "parameters": {
                key: self.data["parameters"][key]
                for key in parameter_names
                if key in self.data.get("parameters", {})
            },
            "training": self.data.get("final_fit", {}),
            "validation": {
                "accuracy": evaluation.get("accuracy"),
                "frames": validation.get("frames", 0),
                "rows": validation.get("rows", 0),
                "class_metrics": evaluation.get("class_metrics", {}),
                "baselines": evaluation.get("baselines", {}),
                "split": evaluation.get("split", {}),
            },
            "extraction": self.data.get("extraction", {}),
            "model": {
                key: artifacts[key]
                for key in ("model", "metadata", "pointer")
                if key in artifacts
            },
            "model_activated": self.data.get("model_activated", False),
            "cleanup": self.data.get("cleanup", {}),
        }


def publish_model(run, classifier, metadata, model_out):
    import joblib

    model_out = Path(model_out).expanduser().resolve()
    destination = model_out.parent / "models" / run.id
    destination.mkdir(parents=True, exist_ok=False)
    model_path = destination / "model.joblib"
    meta_path = destination / "model.meta.json"
    handle, temporary = tempfile.mkstemp(
        prefix=".model-", suffix=".tmp", dir=destination
    )
    try:
        with os.fdopen(handle, "wb") as model_file:
            joblib.dump(classifier, model_file)
            model_file.flush()
            os.fsync(model_file.fileno())
        os.replace(temporary, model_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    atomic_json(meta_path, metadata)
    artifacts = {
        "model": str(model_path),
        "metadata": str(meta_path),
        "report": str(run.path),
    }
    artifacts["pointer"] = str(model_out.with_suffix(".current.json"))
    return artifacts


def activate_model(run):
    atomic_json(
        run.data["artifacts"]["pointer"],
        {"run_id": run.id, "model_path": f"models/{run.id}/model.joblib"},
    )

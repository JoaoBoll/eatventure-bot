"""Controle de treinos do painel; a aprendizagem continua em IA/train_ai.py."""

from collections import Counter, deque
from contextlib import nullcontext
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from core import config, log
from core.i18n import locale, t
from dataset.layout import INDEX_NAME, record_resolution, shard_roots
from dataset.training_files import trained_sample_ids


logger = log.get("dashboard.training")
RESOLUTION = re.compile(r"(?:[1-9]\d*x[1-9]\d*|legacy)\Z")
BUSY = {"queued", "preparing", "running", "cancelling"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(path):
    try:
        if path.stat().st_size > 2_000_000:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


class TrainingManager:
    def __init__(self, root, state_root, training_session=None):
        self.root = Path(root).resolve()
        self.dataset = self.root / "dataset"
        self.path = Path(state_root) / "training.json"
        self.training_session = training_session or (lambda resolution, pause, scope: nullcontext())
        self._condition = threading.Condition(threading.RLock())
        self._catalog_lock = threading.Lock()
        self._catalog = None
        self._catalog_at = 0.0
        self._process = None
        self._thread = None
        self._closing = False
        self._cancel = threading.Event()
        self._pending = None
        self._active = None
        self._logs = deque(maxlen=200)
        self._last_check = None
        self._scan_error = None
        defaults = {
            "enabled": config.AI_AUTO_TRAIN,
            "training_allowed": True,
            "pause_scope": "resolution",
            "interval_seconds": int(config.AI_TRAIN_CHECK_INTERVAL),
            "min_samples": config.AI_TRAIN_BATCH_SIZE,
            "batch_size": 0,
            "jobs": config.AI_TRAIN_JOBS,
            "trees": 200,
            "pause_bots": config.AI_PAUSE_BOT_WHILE_TRAINING,
        }
        saved = _json(self.path)
        try:
            self._settings = self._validate_settings({**defaults, **saved})
        except ValueError:
            self._settings = defaults
        self._next_check = time.monotonic() + self._settings["interval_seconds"]

    @staticmethod
    def _integer(value, minimum, maximum):
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(t("web.training_invalid_number", minimum=minimum, maximum=maximum))
        return value

    @classmethod
    def _validate_settings(cls, data):
        expected = {"enabled", "interval_seconds", "min_samples", "batch_size", "jobs", "trees", "pause_bots", "training_allowed", "pause_scope"}
        if not isinstance(data, dict) or data.keys() != expected:
            raise ValueError(t("web.training_invalid_settings"))
        if any(type(data[key]) is not bool for key in ("enabled", "pause_bots", "training_allowed")) or data["pause_scope"] not in ("resolution", "all"):
            raise ValueError(t("web.training_invalid_settings"))
        result = dict(data)
        for key, minimum, maximum in (
            ("interval_seconds", 60, 86400), ("min_samples", 20, 1000000),
            ("jobs", 1, 64), ("trees", 10, 2000),
        ):
            result[key] = cls._integer(data[key], minimum, maximum)
        cls._integer(data["batch_size"], 0, 1000000)
        if 0 < data["batch_size"] < 20:
            raise ValueError(t("web.training_batch_too_small"))
        return result

    def start(self):
        with self._condition:
            if self._closing or self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name="dashboard-training", daemon=True)
            self._thread.start()

    def configure(self, data):
        settings = self._validate_settings(data)
        with self._condition:
            if self._closing:
                raise RuntimeError(t("messages.dashboard_stopping"))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, prefix=".training-", suffix=".tmp", delete=False) as output:
                    temporary = Path(output.name)
                    json.dump(settings, output, ensure_ascii=False, indent=2)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            self._settings = settings
            if not settings["training_allowed"] and self._active and self._active["status"] in BUSY:
                self._cancel.set()
                self._active["status"] = "cancelling"
                if self._process is not None:
                    self._terminate(self._process)
            self._next_check = time.monotonic() + settings["interval_seconds"]
            self._condition.notify_all()
        self.start()
        return self.status()

    def catalog(self, refresh=False):
        with self._catalog_lock:
            if self._catalog is not None and not refresh and time.monotonic() - self._catalog_at < 10:
                return self._catalog
            # Consulta somente índices e existência dos arquivos; não abre imagens nem retoma limpezas.
            try:
                trained = trained_sample_ids(self.dataset)
            except (OSError, ValueError, TypeError) as error:
                raise RuntimeError(t("web.training_scan_failed")) from error
            groups = {}
            seen = set()
            ignored = 0
            for shard in shard_roots(self.dataset):
                if not shard.resolve().is_relative_to(self.dataset):
                    continue
                shard_id = "." if shard == self.dataset else shard.name
                try:
                    source = (shard / INDEX_NAME).open(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                with source:
                    for line in source:
                        try:
                            record = json.loads(line)
                            if not isinstance(record, dict) or not isinstance(record.get("image"), str) or not record["image"]:
                                raise ValueError()
                            image = (shard / record["image"]).resolve()
                            if not image.is_relative_to(self.dataset) or not image.is_file():
                                raise ValueError()
                            key = f"{shard_id}/{record.get('id') or image.stem}"
                            if key in seen:
                                continue
                            seen.add(key)
                            resolution = record_resolution({**record, "_shard": shard_id})
                            group = groups.setdefault(resolution, {
                                "resolution": resolution, "available": 0, "pending": 0,
                                "items": 0, "pending_items": 0, "categories": Counter(),
                            })
                            boxes = record.get("boxes") or []
                            if not isinstance(boxes, list):
                                boxes = []
                            categories = Counter(
                                box["category"] for box in boxes
                                if isinstance(box, dict) and isinstance(box.get("category"), str)
                                and isinstance(box.get("width"), (int, float)) and box["width"] > 0
                                and isinstance(box.get("height"), (int, float)) and box["height"] > 0
                            )
                            group["available"] += 1
                            group["items"] += sum(categories.values())
                            group["categories"].update(categories)
                            if key not in trained:
                                group["pending"] += 1
                                group["pending_items"] += sum(categories.values())
                        except (ValueError, OSError, TypeError, UnicodeError):
                            ignored += 1
            model_root = self.root / "IA/models"
            if model_root.is_dir():
                for folder in model_root.iterdir():
                    if folder.is_dir() and RESOLUTION.fullmatch(folder.name):
                        groups.setdefault(folder.name, {"resolution": folder.name, "available": 0, "pending": 0, "items": 0, "pending_items": 0, "categories": {}})
            processed = Counter(key.split("/", 1)[0] for key in trained)
            for resolution, group in groups.items():
                pointer = _json(model_root / resolution / "category.current.json")
                group["model_path"] = f"IA/models/{resolution}/category.joblib"
                group["model_version"] = pointer.get("run_id")
                group["model_exists"] = bool(pointer) or (model_root / resolution / "category.joblib").is_file()
                group["processed"] = max(processed.get("." if resolution == "legacy" else resolution, 0), group["available"] - group["pending"])
                group["categories"] = dict(sorted(group["categories"].items()))
            self._catalog = {"resolutions": [groups[key] for key in sorted(groups)], "ignored_records": ignored, "updated_at": _now()}
            self._catalog_at = time.monotonic()
            return self._catalog

    def reports(self):
        paths = sorted((self.root / "IA/training_reports").glob("*.json"), key=lambda path: path.name, reverse=True)[:30]
        result = []
        for path in paths:
            data = _json(path)
            if not data:
                continue
            evaluation = data.get("evaluation") or {}
            result.append({
                "run_id": data.get("run_id", path.stem), "request_id": data.get("web_request_id"),
                "status": data.get("status"), "resolution": (data.get("parameters") or {}).get("resolution") or "all",
                "trigger": data.get("trigger"), "started_at": data.get("started_at_utc"),
                "finished_at": data.get("finished_at_utc"), "duration": data.get("duration_seconds"),
                "training": data.get("training") or data.get("final_fit") or {},
                "validation": data.get("validation") or {"accuracy": evaluation.get("accuracy"), "macro_f1": evaluation.get("macro_f1")},
                "current_batch": data.get("current_batch") or {}, "replay": data.get("replay") or {},
                "model_activated": data.get("model_activated"),
            })
        return result

    def status(self):
        with self._condition:
            return {
                "settings": dict(self._settings),
                "warnings": (["web.training_global_blocked"] if not self._settings["training_allowed"] else []),
                "active": dict(self._active) if self._active else None,
                "busy": bool(self._active and self._active["status"] in BUSY),
                "last_check": self._last_check,
                "next_check_in": max(0, round(self._next_check - time.monotonic())) if self._settings["enabled"] and self._settings["training_allowed"] else None,
                "logs": list(self._logs), "scan_error": self._scan_error,
            }

    def overview(self, refresh=False):
        return {**self.catalog(refresh), **self.status(), "reports": self.reports()}

    def request(self, data):
        with self._condition:
            if not self._settings["training_allowed"]:
                raise RuntimeError(t("web.training_global_blocked"))
        if not isinstance(data, dict) or data.keys() != {"resolution", "limit", "cleanup"}:
            raise ValueError(t("web.training_invalid_request"))
        resolution = data["resolution"]
        if not isinstance(resolution, str) or len(resolution) > 40 or not RESOLUTION.fullmatch(resolution):
            raise ValueError(t("messages.invalid_training_resolution", resolution=resolution))
        limit = self._integer(data["limit"], 0, 1000000)
        if type(data["cleanup"]) is not bool:
            raise ValueError(t("web.training_invalid_request"))
        group = next((group for group in self.catalog(refresh=True)["resolutions"] if group["resolution"] == resolution), None)
        available = group["available"] if group else 0
        if min(available, limit or available) < 20:
            raise ValueError(t("web.training_not_enough_samples"))
        with self._condition:
            if self._closing:
                raise RuntimeError(t("messages.dashboard_stopping"))
            if not self._settings["training_allowed"]:
                raise RuntimeError(t("web.training_global_blocked"))
            if self._active and self._active["status"] in BUSY:
                raise RuntimeError(t("web.training_busy"))
            self._pending = self._job(resolution, False, limit, data["cleanup"], locale())
            self._active = self._pending
            self._cancel.clear()
            self._logs.clear()
            self._condition.notify_all()
        self.start()
        return self.status()

    def _job(self, resolution, automatic, limit=0, cleanup=True, language=None):
        return {
            "id": uuid.uuid4().hex, "resolution": resolution, "automatic": automatic,
            "limit": limit, "cleanup": cleanup, "language": language or locale(),
            "settings": dict(self._settings), "status": "queued", "created_at": _now(),
            "started_at": None, "finished_at": None, "exit_code": None, "error": None,
        }

    def cancel(self):
        with self._condition:
            if self._active and self._active["status"] in BUSY:
                self._cancel.set()
                self._active["status"] = "cancelling"
                self._condition.notify_all()
                process = self._process
            else:
                process = None
        if process is not None:
            self._terminate(process)
        return self.status()

    def stop(self):
        with self._condition:
            self._closing = True
            self._condition.notify_all()
        self.cancel()
        if self._thread is not None:
            self._thread.join(timeout=15)

    @staticmethod
    def _terminate(process):
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        except ProcessLookupError:
            pass

    def _run(self):
        while True:
            with self._condition:
                while not self._closing and self._pending is None:
                    delay = self._next_check - time.monotonic() if self._settings["enabled"] and self._settings["training_allowed"] else None
                    if delay is not None and delay <= 0:
                        break
                    self._condition.wait(timeout=delay)
                if self._closing:
                    return
                job = self._pending
                self._pending = None
                settings = dict(self._settings)
                if job is None:
                    self._last_check = _now()
                    self._next_check = time.monotonic() + settings["interval_seconds"]
            if job is None:
                try:
                    groups = self.catalog(refresh=True)["resolutions"]
                    group = next((group for group in groups if group["pending"] >= settings["min_samples"]), None)
                    with self._condition:
                        self._scan_error = None
                        if self._closing or self._pending is not None or group is None or not self._settings["training_allowed"] or not self._settings["enabled"]:
                            continue
                        job = self._job(group["resolution"], True)
                        self._active = job
                        self._cancel.clear()
                        self._logs.clear()
                except Exception:
                    logger.exception(t("web.training_scan_failed"))
                    with self._condition:
                        self._scan_error = "web.training_scan_failed"
                    continue
            self._execute(job)

    def _output(self, process, job_id):
        try:
            for line in iter(lambda: process.stdout.readline(8192), ""):
                text = line.rstrip("\r\n")[:4096]
                with self._condition:
                    if self._active and self._active["id"] == job_id:
                        self._logs.append(text)
                logger.info("%s", text)
        except (OSError, ValueError):
            pass
        finally:
            process.stdout.close()

    def _execute(self, job):
        settings = job["settings"]
        command = [
            sys.executable, "-u", str(self.root / "IA/train_ai.py"),
            "--kind", "category", "--dataset-root", str(self.dataset),
            "--resolution", job["resolution"], "--jobs", str(settings["jobs"]),
            "--trees", str(settings["trees"]), "--locale", job["language"],
            "--model-out", str(self.root / "IA/models" / job["resolution"] / "category.joblib"),
            "--cleanup" if job["cleanup"] else "--keep-data",
        ]
        if job["automatic"]:
            command.extend(["--automatic", "--min-samples", str(settings["min_samples"])])
            if settings["batch_size"]:
                command.extend(["--batch-size", str(settings["batch_size"])])
        elif job["limit"]:
            command.extend(["--limit", str(job["limit"])])
        process = None
        reader = None
        try:
            with self._condition:
                if not self._cancel.is_set():
                    job["status"] = "preparing"
            if self._cancel.is_set():
                return
            with self.training_session(job["resolution"], settings["pause_bots"], settings["pause_scope"]):
                try:
                    with self._condition:
                        if self._closing or self._cancel.is_set() or not self._settings["training_allowed"]:
                            self._cancel.set()
                            return
                        environment = {**os.environ, "PYTHONUNBUFFERED": "1", "EATVENTURE_LOCALE": job["language"], "EATVENTURE_TRAIN_REQUEST_ID": job["id"]}
                        process = subprocess.Popen(
                            command, cwd=self.root, env=environment, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace",
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                        )
                        self._process = process
                        job.update(status="running", started_at=_now())
                    reader = threading.Thread(target=self._output, args=(process, job["id"]), name="training-output", daemon=True)
                    reader.start()
                    code = process.wait()
                    with self._condition:
                        job["exit_code"] = code
                finally:
                    if process is not None:
                        self._terminate(process)
                    if reader is not None:
                        reader.join(timeout=3)
        except Exception as error:
            logger.exception(t("web.training_run_failed"))
            with self._condition:
                job["error"] = str(error)
        finally:
            with self._condition:
                if self._cancel.is_set():
                    job["status"] = "cancelled"
                elif job["error"] is not None or job["exit_code"] != 0:
                    job["status"] = "failed"
                else:
                    job["status"] = "success"
                job["finished_at"] = _now()
                self._process = None
                self._condition.notify_all()
            with self._catalog_lock:
                self._catalog_at = 0.0
            try:
                self._finish_report(job)
            except OSError:
                logger.exception(t("web.training_report_failed"))

    def _finish_report(self, job):
        for path in sorted((self.root / "IA/training_reports").glob("*.json"), reverse=True)[:30]:
            report = _json(path)
            if report.get("web_request_id") != job["id"]:
                continue
            if report.get("status") == "success":
                with self._condition:
                    job["status"] = "success"
                return
            if report.get("status") in {"running", "publishing"} or job["status"] == "cancelled":
                report.update(status=job["status"], finished_at_utc=job["finished_at"])
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".report-", suffix=".tmp", delete=False) as output:
                        temporary = Path(output.name)
                        json.dump(report, output, ensure_ascii=False)
                    os.replace(temporary, path)
                except OSError:
                    logger.exception(t("web.training_report_failed"))
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            return
        if job["status"] == "success":
            with self._condition:
                job["status"] = "skipped"

"""Prévia web sob demanda, com autorização temporária por sessão."""

import json
import logging
import math
import os
from pathlib import Path
import tempfile
import threading
import time


_MAX_JSON_BYTES = 4096
MIN_FRAME_INTERVAL = 0.05
logger = logging.getLogger("eatventure.preview")


def preview_paths(status_file):
    status_file = Path(status_file)
    suffix = ".status.json"
    name = status_file.name[:-len(suffix)] if status_file.name.endswith(suffix) else status_file.stem
    return {
        "request": status_file.with_name(f"{name}.vision.request.json"),
        "image": status_file.with_name(f"{name}.vision.jpg"),
        "metadata": status_file.with_name(f"{name}.vision.meta.json"),
    }


def _read_json(path):
    with path.open("r", encoding="utf-8") as source:
        raw = source.read(_MAX_JSON_BYTES + 1)
    if len(raw) > _MAX_JSON_BYTES:
        raise ValueError("Metadados da prévia excedem o limite.")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Metadados da prévia inválidos.")
    return value


def _encode_jpeg(frame):
    import cv2

    success, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    return encoded.tobytes() if success else None


class FramePreview:
    def __init__(self, status_file, session_id):
        self.paths = preview_paths(status_file)
        self.session_id = session_id
        self._checked_at = float("-inf")
        self._expires_at = 0.0
        self._last_publish = float("-inf")
        self._owned_image = None
        self._active = False
        self._condition = threading.Condition(threading.RLock())
        self._write_lock = threading.RLock()
        self._publish_lock = threading.Lock()
        self._thread = None
        self._pending = None
        self._stopped = False
        self._needs_clear = False
        self._last_render = float("-inf")
        self._last_warning = {}

    def requested(self):
        with self._condition:
            if self._stopped:
                return False
            monotonic = time.monotonic()
            now = time.time()
            if monotonic - self._checked_at >= 0.25:
                self._checked_at = monotonic
                self._expires_at = 0.0
                try:
                    request = _read_json(self.paths["request"])
                    expires_at = float(request.get("expires_at", 0))
                    if (self.session_id and request.get("session_id") == self.session_id
                            and math.isfinite(expires_at) and 0 < expires_at - now <= 15):
                        self._expires_at = expires_at
                except (OSError, ValueError, TypeError, UnicodeError):
                    pass
            active = now < self._expires_at <= now + 15
            clear = self._active and not active
            self._active = active
            if clear and self._thread is not None and self._thread.is_alive():
                self._needs_clear = True
                self._condition.notify()
                clear = False
        if clear:
            self.clear()
        return active

    def due(self):
        if not self.requested():
            return False
        with self._condition:
            return time.monotonic() - self._last_publish >= MIN_FRAME_INTERVAL - 1e-9

    def _start_locked(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="web-preview", daemon=True)
            self._thread.start()

    def start(self):
        if not self.requested():
            return False
        with self._condition:
            if self._stopped:
                return False
            self._start_locked()
            return True

    def submit(self, frame, render=None):
        with self._condition:
            if self._stopped or not self._active or not time.time() < self._expires_at:
                return False
            self._pending = (frame, render)
            self._start_locked()
            self._condition.notify()
            return True

    def _run(self):
        try:
            while True:
                active = self.requested()
                with self._condition:
                    if self._stopped:
                        break
                    active = active and self._active
                    clear = self._needs_clear
                    self._needs_clear = False
                    if not active:
                        self._pending = None
                    remaining = MIN_FRAME_INTERVAL - (
                        time.monotonic() - max(self._last_publish, self._last_render)
                    ) - 1e-9
                    if not clear and (not active or self._pending is None or remaining > 0):
                        self._condition.wait(timeout=min(0.25, remaining) if active and self._pending is not None else 0.25)
                        continue
                    pending = self._pending if active and remaining <= 0 else None
                    if pending is not None:
                        self._pending = None
                        self._last_render = time.monotonic()
                if clear:
                    self.clear()
                if pending is not None:
                    frame, render = pending
                    try:
                        if render is not None:
                            frame = render(frame)
                        if frame is not None:
                            self.publish(frame)
                    except Exception as error:
                        self._warn_render(error)
        finally:
            self.clear()

    def _warn_render(self, error):
        now = time.monotonic()
        with self._condition:
            if now - self._last_warning.get(type(error), float("-inf")) < 5:
                return
            self._last_warning[type(error)] = now
        logger.warning("Falha desenhando a visão IA: %s", error, exc_info=True)

    def stop(self):
        with self._condition:
            if self._stopped:
                return
            self._stopped = True
            self._active = False
            self._pending = None
            thread = self._thread
            self._condition.notify_all()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        if thread is None or not thread.is_alive():
            self.clear()

    def publish(self, frame):
        if not self._publish_lock.acquire(blocking=False):
            return False
        temporary = []
        try:
            if not self.due():
                return False
            with self._condition:
                if self._stopped:
                    return False
                self._last_publish = time.monotonic()
            encoded = _encode_jpeg(frame)
            if not encoded or not self.requested():
                return False
            metadata = {
                "session_id": self.session_id,
                "pid": os.getpid(),
                "updated_at": time.time(),
            }
            with self._write_lock:
                with self._condition:
                    if self._stopped or not self._active:
                        return False
                self.paths["image"].parent.mkdir(parents=True, exist_ok=True)
                for key, content in (
                    ("image", encoded),
                    ("metadata", json.dumps(metadata, allow_nan=False).encode("utf-8")),
                ):
                    with tempfile.NamedTemporaryFile(mode="wb", dir=self.paths[key].parent, prefix=".vision-", delete=False) as output:
                        path = Path(output.name)
                        temporary.append(path)
                        output.write(content)
                    os.replace(path, self.paths[key])
                    if key == "image":
                        stat = self.paths[key].stat()
                        self._owned_image = (stat.st_mtime_ns, stat.st_size)
            return True
        except Exception:
            # Falhas na visualização opcional não devem interromper ações ou coleta.
            return False
        finally:
            for path in temporary:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            self._publish_lock.release()

    def clear(self):
        with self._write_lock:
            self._clear_owned()

    def _clear_owned(self):
        try:
            metadata = _read_json(self.paths["metadata"])
        except FileNotFoundError:
            try:
                stat = self.paths["image"].stat()
                if self._owned_image == (stat.st_mtime_ns, stat.st_size):
                    self.paths["image"].unlink(missing_ok=True)
            except OSError:
                pass
            return
        except (OSError, ValueError, TypeError, UnicodeError):
            return
        if not self.session_id or metadata.get("session_id") != self.session_id:
            return
        for key in ("image", "metadata"):
            try:
                self.paths[key].unlink(missing_ok=True)
            except OSError:
                pass
        self._owned_image = None

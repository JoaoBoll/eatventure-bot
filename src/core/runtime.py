"""Telemetria e parada cooperativa dos processos iniciados pelo painel."""

import json
import os
from pathlib import Path
import signal
import tempfile
import threading
import time


class RuntimeControl:
    def __init__(self, device_id, status_file, stop_file, session_id=""):
        self.device_id = device_id
        self.status_file = Path(status_file)
        self.stop_file = Path(stop_file)
        self.session_id = session_id
        self.stopped = threading.Event()
        self.last_update = 0.0

    def install_signals(self):
        def stop(signum, frame):
            self.stopped.set()
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), stop)

    def should_stop(self):
        if self.stopped.is_set():
            return True
        try:
            with self.stop_file.open(encoding="utf-8") as source:
                return source.read(128).strip() == self.session_id
        except (OSError, UnicodeError):
            return False

    def publish(self, summary, *, fps=0.0, capture_fps=0.0, templates=0, battery=None, force=False):
        if not force and time.monotonic() - self.last_update < 0.5:
            return
        self.last_update = time.monotonic()
        values = battery or (None, None, None)
        payload = {
            "pid": os.getpid(), "device_id": self.device_id, "session_id": self.session_id,
            "updated_at": time.time(), "state": summary.get("estado", "iniciando"),
            "summary": summary, "fps": fps, "capture_fps": capture_fps, "templates": templates,
            "battery": dict(zip(("level", "charging", "age"), values)),
            **{key: summary.get(key, 0) for key in ("voos", "reformas", "acoes")},
        }
        temporary = None
        try:
            self.status_file.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.status_file.parent, delete=False) as output:
                temporary = Path(output.name)
                json.dump(payload, output, ensure_ascii=False, allow_nan=False)
            os.replace(temporary, self.status_file)
        except (OSError, TypeError, ValueError):
            # Telemetria indisponível não deve interromper ações/coleta já em andamento.
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

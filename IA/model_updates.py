"""Publicações imutáveis e carregamento de modelos fora do loop de captura."""

import json
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelPublication:
    model_path: Path
    run_id: str | None = None


def resolve_publication(path):
    requested = Path(path).expanduser().resolve()
    pointer = requested.with_suffix(".current.json")
    try:
        manifest = json.loads(pointer.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ModelPublication(requested)

    if not isinstance(manifest, dict):
        raise ValueError(f"Publicação inválida: {pointer}")

    run_id = manifest.get("run_id")
    model_path = manifest.get("model_path")
    if (
        not isinstance(run_id, str)
        or not run_id
        or run_id in (".", "..")
        or any(character in run_id for character in "/\\:")
        or not isinstance(model_path, str)
        or not model_path
    ):
        raise ValueError(f"Publicação sem versão ou caminho válido: {pointer}")

    expected = (pointer.parent / "models" / run_id / "model.joblib").resolve()
    published = (pointer.parent / model_path).resolve()
    if Path(model_path).is_absolute() or published != expected:
        raise ValueError(f"Caminho inválido para a publicação {run_id}")

    return ModelPublication(published, run_id)


class ModelWatcher:
    def __init__(self, path, initial_model, loader, *, logger, interval=2.0):
        self.path = Path(path).expanduser().resolve()
        self.loader = loader
        self.logger = logger
        self.interval = max(0.1, interval)
        self._last_attempt = initial_model.meta.get("run_id")
        self._reported_errors = set()
        self._pending = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._watch,
            name="model-updates",
            daemon=True,
        )
        self._thread.start()

    def take_pending(self):
        with self._lock:
            model = self._pending
            self._pending = None
            return model

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _watch(self):
        while not self._stop.is_set():
            publication = None
            try:
                publication = resolve_publication(self.path)
                if publication.run_id and publication.run_id != self._last_attempt:
                    self._last_attempt = publication.run_id
                    model = self.loader(publication)
                    if not self._stop.is_set():
                        with self._lock:
                            self._pending = model
            except (Exception, SystemExit) as error:
                key = (
                    ("version", publication.run_id)
                    if publication is not None
                    else ("manifest", str(error))
                )
                if key not in self._reported_errors:
                    self._reported_errors.add(key)
                    self.logger.warning(
                        "Atualização de modelo rejeitada; mantendo o anterior: %s",
                        error,
                    )
            self._stop.wait(self.interval)

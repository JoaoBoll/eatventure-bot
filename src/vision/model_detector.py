"""Modelo treinado integrado à captura, ações e prévia do bot principal."""

from pathlib import Path
from core.i18n import t
import sys

from core import config, log
from vision.detector import Detector


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from IA.bot_ai import Model, ProposerSource, detect_with_model  # noqa: E402
from IA.model_updates import ModelWatcher, resolve_publication  # noqa: E402


logger = log.get("model_detector")


class ModelDetector(Detector):
    mode = "ai"

    def __init__(self, *, block_regions=None):
        super().__init__(block_regions=block_regions, learn_resolution_templates=False)
        self.model = None
        self.model_label = t("messages.waiting_resolution")
        self._model_path = None
        self._watcher = None
        self._source = ProposerSource() if config.AI_SOURCE == "proposer" else None

    @staticmethod
    def _load_model(publication):
        try:
            model = Model.load_publication(publication)
        except SystemExit as error:
            raise RuntimeError(str(error)) from error
        if model.kind != "category":
            raise RuntimeError(t("messages.category_model_required"))
        return model

    def _model_for(self, width, height):
        path = (
            ROOT / "IA" / "models" / f"{width}x{height}" / "category.joblib"
            if config.AI_MODEL == "auto" else ROOT / config.AI_MODEL
        )
        if path != self._model_path:
            if self._watcher is not None:
                self._watcher.stop()
                self._watcher = None
            model = self._load_model(resolve_publication(path))
            self.model = model
            self._model_path = path
            self._watcher = ModelWatcher(path, model, self._load_model, logger=logger)
            self._watcher.start()
            logger.info(t("messages.ai_active"), path, config.AI_SOURCE)
        updated = self._watcher.take_pending()
        if updated is not None:
            self.model = updated
            logger.info(t("messages.model_updated"), updated.meta.get("run_id"))
        self.model_label = f"{path.parent.name}/{path.name}: {self.model.meta.get('run_id') or 'local'}"
        return self.model

    def detect(self, frame, categories=None, exhaustive=False):
        if frame is None:
            return []
        height, width = frame.shape[:2]
        self._last_frame_size = (width, height)
        model = self._model_for(width, height)
        if self._source is None:
            # O primeiro acerto por template pode ser rejeitado pelo modelo;
            # não interrompe a busca antes de classificar as outras regiões.
            boxes = super().detect(frame, categories, exhaustive=True)
        else:
            boxes = self._source.boxes(frame)
            self.last_searched = self.last_total = len(boxes)
        detections = detect_with_model(model, frame, boxes, config.AI_MIN_CONFIDENCE)
        allowed = set(categories) if categories is not None else None
        filtered = []
        for detection in detections:
            category = detection["category"]
            if allowed is not None and category not in allowed:
                continue
            x, y = detection["x"], detection["y"]
            right, bottom = x + detection["width"], y + detection["height"]
            if category in config.BLOCK_CATEGORIES and any(
                x < (bx + bw) * width and right > bx * width
                and y < (by + bh) * height and bottom > by * height
                for bx, by, bw, bh in self.block_regions
            ):
                continue
            filtered.append(detection)
        filtered = self._suppress(filtered)
        filtered.sort(key=lambda detection: detection["confidence"], reverse=True)
        return filtered

    def stop(self, timeout=5.0):
        if self._watcher is not None:
            self._watcher.stop()
        super().stop(timeout)

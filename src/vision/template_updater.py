"""Gera e atualiza templates por resolução a partir dos defaults."""

import cv2

from vision.detector import Detector


class DefaultTemplateScanner(Detector):

    def __init__(self, parent):
        self._parent = parent
        self._parent_defaults_generation = -1
        self._discovery_selection = None
        self._discovery_cursors = [0, 0]
        self._discovery_round = 0
        self.discovery_missing = True
        super().__init__(
            threshold=parent.threshold,
            color_threshold=parent.color_threshold,
            category_thresholds=dict(parent.category_thresholds),
            category_rois=dict(parent.category_rois),
            block_regions=parent.block_regions,
            coarse_scale=parent.coarse_scale,
            coarse_margin=parent.coarse_margin,
            learn_resolution_templates=False,
        )

    def load_templates(self):
        with self._parent._learn_lock:
            self.templates = [dict(item) for item in self._parent.templates]
            self._parent_defaults_generation = self._parent._defaults_generation

        self.by_category = {}
        for template in self.templates:
            self.by_category.setdefault(template["category"], []).append(template)

        self._scaled_cache.clear()
        self._defaults_cache.clear()
        self._default_variants_cache.clear()
        self._defaults_selection.clear()
        self._roi_cache.clear()
        self._escala_ativa.clear()
        self._sonda.clear()
        self._secas.clear()
        self._quentes.clear()
        self._ordem_cache.clear()
        self._quentes_versao = 0

    def _maybe_reload_templates(self):
        if self._parent_defaults_generation != self._parent._defaults_generation:
            self.load_templates()

    def _templates_for(self, frame_width, frame_height):
        return self._defaults_for(frame_width, frame_height, self._discovery_selection)

    def detect(self, frame, categories=None, exhaustive=False):
        return super().detect(frame, categories=categories, exhaustive=exhaustive)

    def _color_similarity(self, frame, template, x, y):
        crop = frame[y:y + template["height"], x:x + template["width"]]
        reference = template["image"]
        if crop.shape != reference.shape:
            return 0.0

        # A correlação pode continuar alta sobre uma tela escurecida.
        # Limita diferenças espalhadas e oclusões localizadas, como texto.
        difference = cv2.absdiff(crop, reference).max(axis=2)
        valid = template["mask_valid"]
        if valid is not None:
            difference = difference[valid]
        if (
            not difference.size
            or difference.mean() > 24
            or (difference > 48).mean() > 0.10
        ):
            return 0.0

        mask = template["mask"]
        _, crop_contrast = cv2.meanStdDev(crop, mask=mask)
        _, reference_contrast = cv2.meanStdDev(reference, mask=mask)
        if crop_contrast.mean() < 0.60 * reference_contrast.mean():
            return 0.0

        return super()._color_similarity(frame, template, x, y)

    def detect_discovery(self, frame):
        self._maybe_reload_templates()
        height, width = frame.shape[:2]
        with self._parent._learn_lock:
            known = {
                (category, self._base_name(template["name"]))
                for category, templates in self._parent._resolution_templates.get((width, height), {}).items()
                for template in templates
            }

        groups = [[], []]
        for template in self.templates:
            identity = (template["category"], self._base_name(template["name"]))
            groups[identity in known].append(template)

        self.discovery_missing = bool(groups[0])

        # Reserva rodadas para atualizar os templates já conhecidos.
        group_index = int(self._discovery_round % 3 == 2)
        self._discovery_round += 1
        if not groups[group_index]:
            group_index = 1 - group_index
        group = groups[group_index]
        if not group:
            return [], ()

        start = self._discovery_cursors[group_index] % len(group)
        count = min(4, len(group))
        selected = [group[(start + offset) % len(group)] for offset in range(count)]
        self._discovery_cursors[group_index] = (start + count) % len(group)
        categories = tuple(dict.fromkeys(template["category"] for template in selected))
        self._discovery_selection = selected
        self._prefer_active_scale = True
        try:
            return self.detect(frame, categories=categories, exhaustive=True), categories
        finally:
            self._prefer_active_scale = False
            self._discovery_selection = None

"""Gera e atualiza templates por resolução a partir dos defaults."""

from itertools import chain

import cv2

from vision.detector import Detector


class DefaultTemplateScanner(Detector):

    def __init__(self, parent):
        self._parent = parent
        self._parent_defaults_generation = -1
        self._discovery_selection = None
        self._discovery_seen = {}
        self._discovery_round = 0
        self.discovery_progress = (0, 0)
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
        self._discovery_seen.clear()
        self._discovery_round = 0
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

    def detect_discovery(self, frame, categories=None):
        self._maybe_reload_templates()
        height, width = frame.shape[:2]
        all_categories = tuple(self.by_category)
        priority = all_categories if categories is None else tuple(dict.fromkeys(categories))
        context = (width, height)
        with self._parent._learn_lock:
            overrides = self._parent._resolution_templates.get((width, height), {})
            known = {
                (category, self._base_name(template["name"]))
                for category in all_categories
                for template in overrides.get(category, ())
            }

        templates = list(chain.from_iterable(self.by_category.values()))
        self.discovery_missing = any(
            (item["category"], self._base_name(item["name"])) not in known
            for item in templates
        )
        if not templates:
            self.discovery_progress = (0, 0)
            return [], ()

        seen = self._discovery_seen.setdefault(context, {})
        self._discovery_round += 1
        # A rodada global impede que mudanças de estado escondam categorias.
        pool = templates if self._discovery_round % 4 == 0 else [
            item for item in templates if item["category"] in priority
        ] or templates
        missing = self._discovery_round % 3 != 0
        group = [
            item for item in pool
            if ((item["category"], self._base_name(item["name"])) not in known) == missing
        ] or pool

        def identity(item):
            return item["category"], item["name"]

        selected = sorted(group, key=lambda item: seen.get(identity(item), 0))[:4]
        for item in selected:
            seen[identity(item)] = self._discovery_round
        self.discovery_progress = (sum(identity(item) in seen for item in templates), len(templates))
        selected_categories = {template["category"] for template in selected}
        categories = tuple(category for category in all_categories if category in selected_categories)
        self._discovery_selection = selected
        self._prefer_active_scale = self._discovery_round % 5 != 0
        try:
            return self.detect(frame, categories=categories, exhaustive=True), categories
        finally:
            self._prefer_active_scale = False
            self._discovery_selection = None

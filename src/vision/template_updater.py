"""Busca isolada dos defaults que já possuem override na resolução atual."""

from vision.detector import Detector


class DefaultTemplateScanner(Detector):

    def __init__(self, parent):
        self._parent = parent
        self._parent_defaults_generation = -1
        super().__init__(
            threshold=parent.threshold,
            color_threshold=parent.color_threshold,
            category_thresholds=dict(parent.category_thresholds),
            category_rois=dict(parent.category_rois),
            coarse_scale=parent.coarse_scale,
            coarse_margin=parent.coarse_margin,
            use_defaults=True,
            learn_resolution_templates=False,
        )
        self._prefer_active_scale = True

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
        with self._parent._learn_lock:
            overrides = self._parent._resolution_templates.get(
                (frame_width, frame_height), {}
            )
            covered = {
                (category, self._base_name(template["name"]))
                for category, templates in overrides.items()
                for template in templates
            }

        if not covered:
            key = (frame_width, frame_height)
            self._defaults_cache.pop(key, None)
            self._default_variants_cache.pop(key, None)
            self._defaults_selection.pop(key, None)
            return {}

        selected = [
            template for template in self.templates
            if (template["category"], self._base_name(template["name"])) in covered
        ]
        return self._defaults_for(frame_width, frame_height, selected)

    def detect(self, frame):
        return super().detect(frame, categories=None)

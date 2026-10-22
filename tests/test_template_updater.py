"""Atualização em segundo plano com pixels sintéticos e sem assets."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vision import detector as vd  # noqa: E402
from vision.template_updater import DefaultTemplateScanner  # noqa: E402


class DefaultTemplateScannerTests(unittest.TestCase):

    def setUp(self):
        patches = (
            patch.object(vd.Detector, "load_templates"),
            patch.object(cv2, "imread", side_effect=AssertionError("asset lido")),
            patch.object(cv2, "imwrite", side_effect=AssertionError("arquivo gravado")),
            patch.object(vd.threading.Thread, "start", side_effect=AssertionError("thread iniciada")),
            patch.multiple(
                vd,
                REFERENCE_WIDTH=108,
                REFERENCE_HEIGHT=240,
                TEMPLATE_SCALE_BASIS="short_side",
                TEMPLATE_SCALE_STEPS=(1.0,),
                TEMPLATES_WATCH_INTERVAL=0,
                DETECTOR_DEBUG_MISSES=False,
                CATEGORY_COLOR_THRESHOLDS={},
            ),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.parent = vd.Detector(
            threshold=0.999,
            color_threshold=0.999,
            category_thresholds={"box": 0.995},
            category_rois={"box": (0.0, 0.0, 1.0, 1.0)},
            coarse_scale=1.0,
            learn_resolution_templates=False,
        )
        self.parent._defaults_generation = 0
        self.image = np.random.default_rng(42).integers(
            0, 256, (12, 10, 3), dtype=np.uint8
        )

    def template(self, category="box", name="item_001.png", origin="default"):
        return self.parent._build_template(category, name, self.image, None, origin)

    def cover(self, category="box", name="item_001.png", resolution=(108, 240)):
        self.parent._resolution_templates.setdefault(resolution, {}).setdefault(
            category, []
        ).append(self.template(category, name, "override"))

    def test_only_covered_defaults_are_selected_for_current_resolution(self):
        self.parent.templates = [
            self.template(name="108x240_item_001.png"),
            self.template(name="item_002.png"),
            self.template(category="food"),
        ]
        self.cover()
        scanner = DefaultTemplateScanner(self.parent)

        selected = scanner._templates_for(108, 240)
        self.assertEqual(list(selected), ["box"])
        self.assertEqual([item["name"] for item in selected["box"]], ["108x240_item_001.png"])
        self.assertTrue(all(item["origin"] == "default" for item in selected["box"]))
        self.assertEqual(scanner._templates_for(72, 160), {})
        self.assertNotIn((72, 160), scanner._defaults_cache)

    def test_new_or_removed_override_changes_selection_without_default_reload(self):
        self.parent.templates = [self.template()]
        scanner = DefaultTemplateScanner(self.parent)
        self.assertEqual(scanner._templates_for(108, 240), {})
        self.cover()
        self.assertEqual(len(scanner._templates_for(108, 240)["box"]), 1)
        self.parent._resolution_templates.clear()
        self.assertEqual(scanner._templates_for(108, 240), {})
        self.assertNotIn((108, 240), scanner._defaults_cache)
        self.assertNotIn((108, 240), scanner._default_variants_cache)
        self.assertNotIn((108, 240), scanner._defaults_selection)

    def test_default_generation_refreshes_snapshot_and_private_caches(self):
        self.parent.templates = [self.template()]
        self.cover()
        scanner = DefaultTemplateScanner(self.parent)
        old = scanner._defaults_for(108, 240)
        scanner._escala_ativa["old"] = {}
        scanner._sonda["old"] = 1
        scanner._quentes["old"] = {}
        scanner._ordem_cache["old"] = []
        scanner._roi_cache["old"] = ()
        with patch.object(scanner, "load_templates", wraps=scanner.load_templates) as reload:
            scanner._maybe_reload_templates()
            reload.assert_not_called()
            with self.parent._learn_lock:
                self.parent.templates = [self.template(name="item_002.png")]
                self.parent._defaults_generation += 1
            scanner._maybe_reload_templates()
            reload.assert_called_once()

        self.assertEqual([item["name"] for item in scanner.templates], ["item_002.png"])
        self.assertEqual(scanner._parent_defaults_generation, 1)
        self.assertIsNot(scanner.templates, self.parent.templates)
        self.assertIsNot(scanner.templates[0], self.parent.templates[0])
        self.assertIsNot(scanner._defaults_for(108, 240), old)
        for cache in (scanner._escala_ativa, scanner._sonda, scanner._quentes,
                      scanner._ordem_cache, scanner._roi_cache):
            self.assertFalse(cache)

    def test_configuration_and_detection_state_are_isolated(self):
        self.parent.templates = [self.template()]
        self.cover()
        scanner = DefaultTemplateScanner(self.parent)
        self.assertIsNot(scanner.category_thresholds, self.parent.category_thresholds)
        self.assertIsNot(scanner.category_rois, self.parent.category_rois)
        scanner.category_thresholds["box"] = 0.999
        self.assertEqual(self.parent.category_thresholds["box"], 0.995)

        self.parent.last_searched = 71
        self.parent._best = {"sentinel": 0.7}
        self.parent._debug_category = "sentinel"
        self.parent._last_frame_size = (3, 7)
        frame = np.zeros((240, 108, 3), dtype=np.uint8)
        frame[101:113, 31:41] = self.image
        with patch.object(vd, "DETECTOR_DEBUG_MISSES", True):
            detections = scanner.detect(frame)

        self.assertEqual(len(detections), 1)
        self.assertEqual((detections[0]["x"], detections[0]["y"]), (31, 101))
        self.assertEqual(detections[0]["origin"], "default")
        self.assertEqual(self.parent.last_searched, 71)
        self.assertEqual(self.parent._best, {"sentinel": 0.7})
        self.assertEqual(self.parent._debug_category, "sentinel")
        self.assertEqual(self.parent._last_frame_size, (3, 7))
        self.assertEqual(self.parent._defaults_cache, {})
        self.assertEqual(self.parent._escala_ativa, {})
        self.assertTrue(self.parent._learn_queue.empty())

    def test_scanner_never_reads_disk_starts_worker_or_learns(self):
        self.parent.templates = [self.template()]
        self.cover()
        with (
            patch.object(vd.Detector, "_load_templates_locked", side_effect=AssertionError("disco lido")),
            patch.object(vd.Detector, "_templates_signature", side_effect=AssertionError("pastas lidas")),
            patch.object(vd.Detector, "_queue_learn_candidate", side_effect=AssertionError("aprendeu")),
        ):
            scanner = DefaultTemplateScanner(self.parent)
            self.assertFalse(scanner.learn_resolution_templates)
            self.assertFalse(scanner._learn_thread.is_alive())
            frame = np.zeros((240, 108, 3), dtype=np.uint8)
            frame[101:113, 31:41] = self.image
            self.assertTrue(scanner.detect(frame))
            self.parent._defaults_generation += 1
            scanner._maybe_reload_templates()
            self.assertTrue(scanner.stop())


if __name__ == "__main__":
    unittest.main()

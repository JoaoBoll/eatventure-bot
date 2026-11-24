"""Atualizacao de templates com pixels sinteticos e persistencia simulada."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vision import detector as vd  # noqa: E402


class TemplateRefreshTests(unittest.TestCase):

    def setUp(self):
        patches = (
            patch.object(vd.Detector, "load_templates"),
            patch.object(vd.Detector, "_save_fidelity"),
            patch.object(vd.Detector, "_reassina_overrides"),
            patch.object(cv2, "imread", side_effect=AssertionError("asset lido")),
            patch.multiple(
                vd,
                REFERENCE_WIDTH=108,
                REFERENCE_HEIGHT=240,
                TEMPLATE_SCALE_BASIS="short_side",
                TEMPLATE_SCALE_STEPS=(0.92, 1.0, 1.08),
                TEMPLATES_WATCH_INTERVAL=0,
            ),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

        self.detector = vd.Detector(
            threshold=0.90,
            category_thresholds={"box": 0.95},
            category_rois={},
            coarse_scale=1.0,
            learn_resolution_templates=False,
        )
        self.addCleanup(self.detector.stop)
        self.image = np.random.default_rng(73).integers(
            0, 256, (12, 10, 3), dtype=np.uint8
        )
        self.key = (108, 240)
        self.fidelity_id = (self.key, "box", "item_001.png")
        self.template = self.make_template("72x160_item_001.png")

    def make_template(self, name, category="box", origin="default"):
        return self.detector._build_template(
            category, name, self.image.copy(), None, origin
        )

    def queue(self, confidence, template=None):
        self.detector._queue_learn_candidate(
            self.image,
            self.template if template is None else template,
            0,
            0,
            *self.key,
            confidence,
        )

    def test_queue_keeps_best_pending_candidate_within_category_range(self):
        self.detector._fidelity[self.fidelity_id] = 0.99
        confidences = (0.95, 0.95, 1.0, 0.97)

        for confidence in confidences:
            self.queue(confidence)

        self.assertEqual(self.detector._learn_queue.qsize(), 1)
        candidate = self.detector._learn_pending[self.fidelity_id]
        self.assertEqual(candidate[4], max(confidences))
        self.assertEqual(candidate[:3], ("box", "item_001.png", self.key))
        self.image.fill(0)
        self.assertTrue(np.any(candidate[3]))

    def test_queue_rejects_outside_category_range_and_nonfinite_scores(self):
        for confidence in (0.0, 0.90, 0.9499, 1.0001, float("nan"), float("inf"), -float("inf")):
            with self.subTest(confidence=confidence):
                self.queue(confidence)
                self.assertTrue(self.detector._learn_queue.empty())

    def test_foreground_override_match_does_not_queue_another_generation(self):
        override = self.make_template("item_001.png", origin="override")

        self.queue(1.0, override)

        self.assertTrue(self.detector._learn_queue.empty())

    def test_scanner_rejects_dark_occluded_and_flat_crops(self):
        from vision.template_updater import DefaultTemplateScanner

        scanner = DefaultTemplateScanner(self.detector)
        self.addCleanup(scanner.stop)
        reference = np.full((12, 10, 3), 107, dtype=np.uint8)
        reference[:, 1::2] = 147
        template = self.detector._build_template(
            "box", "item_001.png", reference, None, "default"
        )
        occluded = reference.copy()
        occluded[:2] = 255

        self.assertAlmostEqual(scanner._color_similarity(reference, template, 0, 0), 1.0)
        for crop in (reference // 2, occluded, np.full_like(reference, 127)):
            self.assertEqual(scanner._color_similarity(crop, template, 0, 0), 0.0)

    def test_refresh_queues_all_candidates_for_fidelity_comparison(self):
        from unittest.mock import Mock

        other = self.make_template("108x240_item_001.png")
        scanner = Mock()
        scanner.discovery_missing = False
        scanner.detect_discovery.return_value = ([
            {"_template": self.template, "x": 0, "y": 0, "confidence": 0.96},
            {"_template": other, "x": 1, "y": 1, "confidence": 0.99},
        ], ("box",))
        self.detector._refresh_scanner = scanner
        with patch.object(self.detector, "_queue_learn_candidate") as queue:
            self.detector._refresh_defaults(self.image, 3, ["box"])

        scanner.detect_discovery.assert_called_once_with(self.image, ["box"])
        self.assertEqual(queue.call_count, 2)
        self.assertIs(queue.call_args_list[0].args[1], other)
        self.assertIs(queue.call_args_list[1].args[1], self.template)

    def test_queue_uses_global_threshold_for_category_without_override(self):
        template = self.make_template("item_001.png", category="synthetic")
        self.queue(0.8999, template)
        self.queue(0.90, template)
        self.assertEqual(self.detector._learn_queue.qsize(), 1)
        candidate = self.detector._learn_pending[(self.key, "synthetic", "item_001.png")]
        self.assertEqual(candidate[4], 0.90)

    def test_processing_keeps_current_crop_when_fidelity_drops(self):
        current = self.make_template("item_001.png", origin="override")
        self.detector._resolution_templates[self.key] = {"box": [current]}
        self.detector._fidelity[self.fidelity_id] = 0.99
        with patch.object(self.detector, "_write_learn_candidate") as write:
            self.detector._process_learn_candidate(
                "box", "item_001.png", self.key, np.zeros_like(self.image), 0.95
            )
        write.assert_not_called()
        self.assertIs(self.detector._resolution_templates[self.key]["box"][0], current)

    def test_processing_rejects_invalid_score_without_persistence(self):
        self.detector._fidelity[self.fidelity_id] = 0.98
        with (
            patch.object(Path, "mkdir") as mkdir,
            patch.object(cv2, "imwrite", return_value=True) as write,
        ):
            for confidence in (0.94, 1.0001, float("nan"), float("inf"), -float("inf")):
                with self.subTest(confidence=confidence):
                    self.detector._process_learn_candidate(
                        "box", "item_001.png", self.key, self.image, confidence
                    )
                    self.assertEqual(self.detector._fidelity[self.fidelity_id], 0.98)
                    self.assertEqual(self.detector._resolution_templates, {})

            mkdir.assert_not_called()
            write.assert_not_called()

    def test_older_generation_can_win_with_better_fidelity(self):
        current = self.make_template("item_001.png", origin="override")
        self.detector._resolution_templates[self.key] = {"box": [current]}
        self.detector._fidelity[self.fidelity_id] = 0.95
        self.detector._learned_sequence[self.fidelity_id] = 20
        older = np.full_like(self.image, 19)
        with patch.object(self.detector, "_write_learn_candidate") as write:
            self.detector._process_learn_candidate(
                "box", "item_001.png", self.key, older, 1.0, sequence=19
            )
        self.assertEqual(write.call_args.args[4:6], (1.0, 19))

    def test_failed_persistence_preserves_last_learned_template(self):
        previous = self.make_template("item_001.png", origin="override")
        self.detector._resolution_templates[self.key] = {"box": [previous]}
        self.detector._fidelity[self.fidelity_id] = 0.99
        with (
            patch.object(Path, "mkdir"),
            patch.object(cv2, "imwrite", return_value=False),
            patch.object(vd.logger, "warning"),
        ):
            self.detector._process_learn_candidate(
                "box", "item_001.png", self.key, np.zeros_like(self.image), 0.95
            )

        self.assertIs(self.detector._resolution_templates[self.key]["box"][0], previous)
        self.assertEqual(self.detector._fidelity[self.fidelity_id], 0.99)

    def test_watcher_waits_for_write_and_keeps_committed_fidelity(self):
        self.detector._fidelity[self.fidelity_id] = 0.99
        self.detector._templates_signature_cache = ("defaults", "overrides")

        def write_in_progress(*_args):
            self.assertTrue(self.detector._learn_writing)
            self.detector._maybe_reload_templates()
            signature.assert_not_called()
            self.assertEqual(self.detector._fidelity[self.fidelity_id], 0.99)
            self.detector._fidelity[self.fidelity_id] = 0.95

        with (
            patch.object(self.detector, "_write_learn_candidate", side_effect=write_in_progress),
            patch.object(vd, "TEMPLATES_WATCH_INTERVAL", 1.0),
            patch.object(vd.time, "monotonic", side_effect=(100.0, 102.0)),
            patch.object(
                self.detector,
                "_templates_signature",
                return_value=self.detector._templates_signature_cache,
            ) as signature,
            patch.object(
                self.detector,
                "_load_templates_locked",
                side_effect=AssertionError("disco lido"),
            ),
        ):
            self.detector._process_learn_candidate(
                "box", "item_001.png", self.key, self.image, 0.95
            )

            self.assertFalse(self.detector._learn_writing)
            self.assertEqual(self.detector._fidelity[self.fidelity_id], 0.95)
            signature.assert_not_called()

            self.detector._maybe_reload_templates()
            signature.assert_called_once_with()

    def test_write_exception_releases_watcher_guard(self):
        with patch.object(self.detector, "_write_learn_candidate", side_effect=OSError("falha simulada")):
            with self.assertRaisesRegex(OSError, "falha simulada"):
                self.detector._process_learn_candidate(
                    "box", "item_001.png", self.key, self.image, 0.95
                )

        self.assertFalse(self.detector._learn_writing)
        self.assertEqual(self.detector._resolution_templates, {})

    def test_foreground_uses_only_resolution_templates(self):
        self.detector.templates = [
            self.make_template("72x160_item_001.png"),
            self.make_template("108x240_item_001.png"),
            self.make_template("item_002.png"),
            self.make_template("item_001.png", category="synthetic"),
        ]
        override = self.make_template("item_001.png", origin="override")
        self.detector._resolution_templates[self.key] = {"box": [override]}

        templates = self.detector._templates_for(*self.key)

        self.assertIs(templates["box"][0], override)
        self.assertEqual(templates["box"], [override])
        self.assertNotIn("synthetic", templates)

        defaults = self.detector._defaults_for(*self.key)["box"]
        self.assertTrue(any(
            self.detector._base_name(item["name"]) == "item_001.png"
            for item in defaults
        ))

    def test_override_does_not_remove_defaults_from_another_resolution(self):
        self.detector.templates = [self.template]
        override = self.make_template("item_001.png", origin="override")
        self.detector._resolution_templates[self.key] = {"box": [override]}

        learned = self.detector._templates_for(*self.key)["box"]
        other = self.detector._templates_for(72, 160).get("box", [])

        self.assertIs(learned[0], override)
        self.assertEqual(learned, [override])
        self.assertEqual(other, [])


if __name__ == "__main__":
    unittest.main()

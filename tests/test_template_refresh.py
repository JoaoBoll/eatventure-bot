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

    def test_queue_keeps_only_latest_pending_candidate_within_category_range(self):
        self.detector._fidelity[self.fidelity_id] = 0.99
        confidences = (0.95, 0.95, 1.0, 0.97)

        for confidence in confidences:
            self.queue(confidence)

        self.assertEqual(self.detector._learn_queue.qsize(), 1)
        candidate = self.detector._learn_pending[self.fidelity_id]
        self.assertEqual(candidate[4], confidences[-1])
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

    def test_queue_uses_global_threshold_for_category_without_override(self):
        template = self.make_template("item_001.png", category="synthetic")
        self.queue(0.8999, template)
        self.queue(0.90, template)
        self.assertEqual(self.detector._learn_queue.qsize(), 1)
        candidate = self.detector._learn_pending[(self.key, "synthetic", "item_001.png")]
        self.assertEqual(candidate[4], 0.90)

    def test_processing_replaces_with_latest_valid_crop_even_when_score_drops(self):
        self.detector._fidelity[self.fidelity_id] = 0.99
        other_resolution = (72, 160)
        untouched_cache = {"synthetic": []}
        self.detector._scaled_cache[other_resolution] = untouched_cache

        with (
            patch.object(Path, "mkdir") as mkdir,
            patch.object(cv2, "imwrite", return_value=True) as write,
        ):
            for index, confidence in enumerate((0.95, 0.95, 1.0, 0.97)):
                with self.subTest(confidence=confidence, index=index):
                    crop = np.full_like(self.image, index + 1)
                    self.detector._scaled_cache[self.key] = {"stale": []}
                    self.detector._process_learn_candidate(
                        "box", "item_001.png", self.key, crop, confidence
                    )

                    templates = self.detector._resolution_templates[self.key]["box"]
                    self.assertEqual(len(templates), 1)
                    self.assertEqual(templates[0]["origin"], "override")
                    np.testing.assert_array_equal(templates[0]["image"], crop)
                    self.assertEqual(self.detector._fidelity[self.fidelity_id], confidence)
                    self.assertNotIn(self.key, self.detector._scaled_cache)
                    self.assertIs(self.detector._scaled_cache[other_resolution], untouched_cache)

            self.assertEqual(write.call_count, 4)
            self.assertEqual(mkdir.call_count, 4)

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

    def test_older_generation_does_not_overwrite_newer_crop(self):
        latest = np.full_like(self.image, 20)
        older = np.full_like(self.image, 19)
        with (
            patch.object(Path, "mkdir"),
            patch.object(cv2, "imwrite", return_value=True) as write,
        ):
            self.detector._process_learn_candidate(
                "box", "item_001.png", self.key, latest, 0.95, sequence=20
            )
            self.detector._process_learn_candidate(
                "box", "item_001.png", self.key, older, 1.0, sequence=19
            )

        write.assert_called_once()
        current = self.detector._resolution_templates[self.key]["box"][0]
        np.testing.assert_array_equal(current["image"], latest)
        self.assertEqual(self.detector._fidelity[self.fidelity_id], 0.95)

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
            return True

        with (
            patch.object(Path, "mkdir"),
            patch.object(cv2, "imwrite", side_effect=write_in_progress),
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
        with (
            patch.object(Path, "mkdir"),
            patch.object(cv2, "imwrite", side_effect=OSError("falha simulada")),
        ):
            with self.assertRaisesRegex(OSError, "falha simulada"):
                self.detector._process_learn_candidate(
                    "box", "item_001.png", self.key, self.image, 0.95
                )

        self.assertFalse(self.detector._learn_writing)
        self.assertEqual(self.detector._resolution_templates, {})

    def test_foreground_omits_all_variants_of_covered_default_only(self):
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
        self.assertTrue(any(item["origin"] == "default" for item in templates["box"]))
        self.assertTrue(all(
            item is override or self.detector._base_name(item["name"]) == "item_002.png"
            for item in templates["box"]
        ))
        self.assertTrue(templates["synthetic"])
        self.assertTrue(all(item["origin"] == "default" for item in templates["synthetic"]))

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
        other = self.detector._templates_for(72, 160)["box"]

        self.assertEqual(len(learned), 1)
        self.assertIs(learned[0], override)
        self.assertTrue(other)
        self.assertTrue(all(item["origin"] == "default" for item in other))


if __name__ == "__main__":
    unittest.main()

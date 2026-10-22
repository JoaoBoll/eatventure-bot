"""Custos de busca e persistencia com pixels sinteticos e I/O bloqueado."""

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


class TemplateEfficiencyTests(unittest.TestCase):

    def setUp(self):
        for item in (
            patch.object(vd.Detector, "load_templates"),
            patch.object(cv2, "imread", side_effect=AssertionError("asset lido")),
            patch.object(cv2, "imwrite", side_effect=AssertionError("arquivo gravado")),
            patch.object(Path, "mkdir", side_effect=AssertionError("pasta criada")),
            patch.object(vd.threading.Thread, "start", side_effect=AssertionError("thread iniciada")),
            patch.multiple(
                vd,
                REFERENCE_WIDTH=108,
                REFERENCE_HEIGHT=240,
                TEMPLATE_SCALE_BASIS="short_side",
                TEMPLATE_SCALE_STEPS=(0.92, 1.0, 1.08),
                TEMPLATES_WATCH_INTERVAL=0,
                DETECTOR_DEBUG_MISSES=False,
                CATEGORY_MATCH_BUDGET={},
                CATEGORY_SCAN_QUOTA={},
                CATEGORY_COLOR_THRESHOLDS={},
                ESCALA_UNICA_POR_TEMPLATE=True,
            ),
        ):
            item.start()
            self.addCleanup(item.stop)

        self.detector = vd.Detector(
            threshold=0.95,
            color_threshold=0.95,
            category_thresholds={"box": 0.95},
            category_rois={},
            coarse_scale=1.0,
            learn_resolution_templates=False,
        )
        self.addCleanup(self.detector.stop)
        self.image = np.random.default_rng(89).integers(
            0, 256, (12, 10, 3), dtype=np.uint8
        )
        self.key = (108, 240)

    def template(self, name="item_001.png", category="box", origin="default"):
        return self.detector._build_template(
            category, name, self.image.copy(), None, origin
        )

    def cover(self, name="item_001.png", category="box"):
        override = self.template(name, category, "override")
        self.detector._resolution_templates.setdefault(self.key, {}).setdefault(
            category, []
        ).append(override)
        return override

    def rescaled_keys(self, calls):
        return {
            (call.args[0]["category"], self.detector._base_name(call.args[0]["name"]))
            for call in calls
        }

    def test_covered_foreground_never_builds_default_variants(self):
        self.detector.templates = [
            self.template("72x160_item_001.png"),
            self.template("108x240_item_001.png"),
            self.template(category="food"),
        ]
        box = self.cover()
        food = self.cover(category="food")

        with patch.object(
            self.detector, "_rescale_template", wraps=self.detector._rescale_template
        ) as rescale:
            selected = self.detector._templates_for(*self.key)
            cached = self.detector._templates_for(*self.key)

        rescale.assert_not_called()
        self.assertEqual(set(selected), {"box", "food"})
        self.assertEqual(len(selected["box"]), 1)
        self.assertEqual(len(selected["food"]), 1)
        self.assertIs(selected["box"][0], box)
        self.assertIs(selected["food"][0], food)
        self.assertIs(cached, selected)
        self.assertEqual((box["width"], box["height"]), (10, 12))

    def test_partial_foreground_builds_only_uncovered_category_and_name(self):
        self.detector.templates = [
            self.template("72x160_item_001.png"),
            self.template("108x240_item_001.png"),
            self.template("item_002.png"),
            self.template(category="food"),
        ]
        override = self.cover()

        with patch.object(
            self.detector, "_rescale_template", wraps=self.detector._rescale_template
        ) as rescale:
            selected = self.detector._templates_for(*self.key)

        self.assertEqual(
            self.rescaled_keys(rescale.call_args_list),
            {("box", "item_002.png"), ("food", "item_001.png")},
        )
        self.assertIs(selected["box"][0], override)
        self.assertTrue(any(item["origin"] == "default" for item in selected["box"]))
        self.assertTrue(all(item["origin"] == "default" for item in selected["food"]))

    def test_background_builds_only_covered_defaults_and_reuses_existing_variants(self):
        self.detector.templates = [
            self.template("72x160_item_001.png"),
            self.template("108x240_item_001.png"),
            self.template("item_002.png"),
            self.template(category="food"),
        ]
        self.cover()
        scanner = DefaultTemplateScanner(self.detector)
        self.addCleanup(scanner.stop)

        with patch.object(
            scanner, "_rescale_template", wraps=scanner._rescale_template
        ) as rescale:
            selected = scanner._templates_for(*self.key)
            self.assertEqual(
                self.rescaled_keys(rescale.call_args_list), {("box", "item_001.png")}
            )
            self.assertEqual(set(selected), {"box"})
            self.assertTrue(all(item["origin"] == "default" for item in selected["box"]))
            rescale.reset_mock()

            scanner._templates_for(*self.key)
            rescale.assert_not_called()

            self.cover("item_002.png")
            selected = scanner._templates_for(*self.key)

        self.assertEqual(
            self.rescaled_keys(rescale.call_args_list), {("box", "item_002.png")}
        )
        self.assertEqual(
            {scanner._base_name(item["name"]) for item in selected["box"]},
            {"item_001.png", "item_002.png"},
        )

    def test_background_reuses_winning_scale_and_recalibrates_after_dry_frames(self):
        self.detector.templates = [self.template()]
        self.cover()
        scanner = DefaultTemplateScanner(self.detector)
        self.addCleanup(scanner.stop)
        frame = np.zeros((self.key[1], self.key[0], 3), dtype=np.uint8)
        desired_scale = 1.08
        dry = False

        def search(_frame, _coarse, template, _threshold, _roi):
            if dry:
                return []
            confidence = 0.99 if template["escala"] == desired_scale else 0.96
            return [(20, 40, confidence)]

        with (
            patch.object(scanner, "_search", side_effect=search) as searches,
            patch.object(scanner, "_color_similarity", return_value=1.0),
            patch.object(vd.time, "monotonic", return_value=10.0),
        ):
            self.assertTrue(scanner.detect(frame))
            self.assertEqual(scanner.last_searched, len(vd.TEMPLATE_SCALE_STEPS))
            searches.reset_mock()

            self.assertTrue(scanner.detect(frame))
            self.assertEqual(scanner.last_searched, 1)
            self.assertEqual(searches.call_args.args[2]["escala"], desired_scale)

            dry = True
            for _ in range(vd.ESCALA_LIBERA_SECAS):
                self.assertEqual(scanner.detect(frame), [])
                self.assertEqual(scanner.last_searched, 1)

            dry = False
            desired_scale = 0.92
            self.assertTrue(scanner.detect(frame))
            self.assertEqual(scanner.last_searched, len(vd.TEMPLATE_SCALE_STEPS))
            searches.reset_mock()

            self.assertTrue(scanner.detect(frame))
            self.assertEqual(scanner.last_searched, 1)
            self.assertEqual(searches.call_args.args[2]["escala"], desired_scale)

    def test_background_updates_all_covered_categories_even_after_a_match(self):
        self.detector.templates = [self.template(), self.template(category="food")]
        self.cover()
        self.cover(category="food")
        scanner = DefaultTemplateScanner(self.detector)
        self.addCleanup(scanner.stop)
        frame = np.zeros((self.key[1], self.key[0], 3), dtype=np.uint8)

        with (
            patch.object(scanner, "_search", return_value=[(20, 40, 0.99)]),
            patch.object(scanner, "_color_similarity", return_value=1.0),
            patch.object(vd, "VISION_PRIORITY_STOP", True),
        ):
            detections = scanner.detect(frame)

        self.assertEqual({item["category"] for item in detections}, {"box", "food"})
        self.assertEqual(scanner.last_searched, 2 * len(vd.TEMPLATE_SCALE_STEPS))

    def test_identical_crop_skips_persistence_and_keeps_latest_accepted_metadata(self):
        override = self.cover()
        fidelity_id = (self.key, "box", "item_001.png")
        self.detector._fidelity[fidelity_id] = 0.99
        self.detector._learned_sequence[fidelity_id] = 9
        cached = {"box": [override]}
        order = [override]
        self.detector._scaled_cache[self.key] = cached
        self.detector._ordem_cache[(self.key, "box")] = order
        version = self.detector._overrides_versao

        with (
            patch.object(self.detector, "_save_fidelity") as save_fidelity,
            patch.object(self.detector, "_reassina_overrides") as signature,
        ):
            for confidence, sequence in ((0.96, 10), (0.95, 11)):
                self.detector._process_learn_candidate(
                    "box", "item_001.png", self.key, self.image.copy(), confidence, sequence
                )
                self.assertEqual(self.detector._fidelity[fidelity_id], confidence)
                self.assertEqual(self.detector._learned_sequence[fidelity_id], sequence)

            self.detector._process_learn_candidate(
                "box", "item_001.png", self.key, self.image.copy(), 0.99, 10
            )

        save_fidelity.assert_not_called()
        signature.assert_not_called()
        self.assertEqual(self.detector._fidelity[fidelity_id], 0.95)
        self.assertEqual(self.detector._learned_sequence[fidelity_id], 11)
        self.assertIs(self.detector._resolution_templates[self.key]["box"][0], override)
        self.assertIs(self.detector._scaled_cache[self.key], cached)
        self.assertIs(self.detector._ordem_cache[(self.key, "box")], order)
        self.assertEqual(self.detector._overrides_versao, version)
        self.assertFalse(self.detector._learn_writing)


if __name__ == "__main__":
    unittest.main()

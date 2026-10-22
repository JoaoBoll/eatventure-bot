"""Regressões de resolução com pixels sintéticos, sem assets ou dispositivos."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vision import detector as vd  # noqa: E402


class DetectorResolutionTests(unittest.TestCase):

    def setUp(self):
        patches = (
            patch.object(vd.Detector, "load_templates"),
            patch.object(vd.threading.Thread, "start"),
            patch.object(cv2, "imread", side_effect=AssertionError("asset lido")),
            patch.multiple(
                vd,
                REFERENCE_WIDTH=108,
                REFERENCE_HEIGHT=240,
                TEMPLATE_SCALE_BASIS="short_side",
                TEMPLATE_SCALE_STEPS=(0.85, 0.92, 1.0, 1.08, 1.15),
                TEMPLATES_WATCH_INTERVAL=0,
                LEARN_RESOLUTION_TEMPLATES=False,
                DETECTOR_DEBUG_MISSES=False,
                CATEGORY_MATCH_BUDGET={},
                CATEGORY_SCAN_QUOTA={},
                CATEGORY_COLOR_THRESHOLDS={},
                ESCALA_UNICA_POR_TEMPLATE=True,
            ),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

        self.detector = vd.Detector(
            threshold=0.995,
            color_threshold=0.995,
            category_thresholds={},
            category_rois={},
            coarse_scale=1.0,
        )
        self.image = np.random.default_rng(42).integers(
            0, 256, (24, 20, 3), dtype=np.uint8
        )

    def template(self, name="item_001.png", image=None):
        return self.detector._build_template(
            "synthetic", name,
            self.image if image is None else image,
            None, "default",
        )

    def test_source_resolution_controls_default_scale(self):
        self.detector.templates = [self.template("72x160_item_001.png")]

        with patch.object(vd, "TEMPLATE_SCALE_STEPS", (1.0,)):
            for width, height, expected in (
                (72, 160, (20, 24)),
                (108, 240, (30, 36)),
                (108, 192, (30, 36)),
                (240, 108, (30, 36)),
            ):
                with self.subTest(resolution=(width, height)):
                    template = self.detector._templates_for(width, height)["synthetic"][0]
                    self.assertEqual((template["width"], template["height"]), expected)

    def test_legacy_default_uses_reference_scale(self):
        self.detector.templates = [self.template()]
        with patch.object(vd, "TEMPLATE_SCALE_STEPS", (1.0,)):
            template = self.detector._templates_for(72, 160)["synthetic"][0]
        self.assertEqual((template["width"], template["height"]), (13, 16))

    def test_reference_keeps_zoom_variants(self):
        self.detector.templates = [self.template()]
        templates = self.detector._templates_for(108, 240)["synthetic"]
        self.assertEqual(len(templates), len(vd.TEMPLATE_SCALE_STEPS))
        self.assertNotIn("escala", self.detector.templates[0])

    def test_resolution_override_keeps_native_size_without_default_fallback(self):
        self.detector.templates = [self.template("72x160_item_001.png")]
        override = self.template("item_001.png", self.image[:10, :8])
        override["origin"] = "override"
        self.detector._resolution_templates[(108, 240)] = {"synthetic": [override]}
        templates = self.detector._templates_for(108, 240)["synthetic"]
        self.assertIs(templates[0], override)
        self.assertEqual((templates[0]["width"], templates[0]["height"]), (8, 10))
        self.assertEqual(len(templates), 1)

    def test_calibration_uses_last_variant_when_it_is_best(self):
        self.detector.templates = [self.template()]
        templates = self.detector._templates_for(108, 240)["synthetic"]
        active = {}
        for index, template in enumerate(templates):
            self.detector._anota_escala(active, template, 0.8 + index * 0.04, 10.0)
        selected = [
            template for template in templates
            if self.detector._na_escala_da_vez(template, active, 10.1)
        ]
        self.assertEqual(selected, [templates[-1]])

    def test_missing_variants_complete_calibration(self):
        self.detector.templates = [self.template()]
        templates = self.detector._templates_for(108, 240)["synthetic"]
        active = {}
        self.detector._anota_escala(active, templates[0], 0.99, 10.0)
        for template in templates[1:]:
            self.detector._anota_escala(active, template, None, 10.1)
        selected = [
            template for template in templates
            if self.detector._na_escala_da_vez(template, active, 10.2)
        ]
        self.assertEqual(selected, [templates[0]])
        self.detector._last_frame_size = (108, 240)
        self.detector._escala_ativa[((108, 240), "synthetic")] = active
        self.assertFalse(self.detector._em_sonda("synthetic"))

    def test_source_defaults_calibrate_independently(self):
        self.detector.templates = [
            self.template("72x160_item_001.png"),
            self.template("108x240_item_001.png"),
        ]
        templates = self.detector._templates_for(108, 240)["synthetic"]
        active = {}
        first = templates[:len(vd.TEMPLATE_SCALE_STEPS)]
        for template in first:
            self.detector._anota_escala(active, template, 0.99, 10.0)
        self.assertTrue(all(
            self.detector._na_escala_da_vez(template, active, 10.1)
            for template in templates[len(first):]
        ))

    def test_tiny_discarded_variants_do_not_block_calibration(self):
        self.detector.templates = [self.template(image=self.image[:4, :4])]
        with patch.object(vd, "TEMPLATE_SCALE_STEPS", (0.5, 1.0, 1.5)):
            templates = self.detector._templates_for(108, 240)["synthetic"]
        self.assertEqual([item["indice_escala"] for item in templates], [0, 1])
        self.assertTrue(all(item["total_escalas"] == 2 for item in templates))

    def test_detection_survives_scale_lock_and_recalibrates_after_dry_frames(self):
        self.detector.templates = [self.template()]
        scaled = cv2.resize(self.image, (13, 16), interpolation=cv2.INTER_AREA)
        frame = np.zeros((160, 72, 3), dtype=np.uint8)
        frame[45:61, 22:35] = scaled

        for _ in range(3):
            detections = self.detector.detect(frame, categories=["synthetic"])
            self.assertTrue(detections)
            self.assertEqual((detections[0]["x"], detections[0]["y"]), (22, 45))
            self.assertEqual((detections[0]["width"], detections[0]["height"]), (13, 16))

        self.assertEqual(self.detector.last_searched, 1)
        for _ in range(vd.ESCALA_LIBERA_SECAS):
            self.assertEqual(self.detector.detect(np.zeros_like(frame), ["synthetic"]), [])
        self.assertFalse(self.detector._escala_ativa[((72, 160), "synthetic")])
        self.assertTrue(self.detector.detect(frame, ["synthetic"]))

    def test_resolution_name_requires_positive_dimensions(self):
        self.assertEqual(self.detector._parse_resolution_dir_name("1080x2400"), (1080, 2400))
        for name in ("0x2400", "1080x0", "-1x2400", "1080x2400x1", "default"):
            with self.subTest(name=name):
                self.assertIsNone(self.detector._parse_resolution_dir_name(name))

    def test_learning_can_be_disabled_without_starting_worker(self):
        with (
            patch.object(vd, "LEARN_RESOLUTION_TEMPLATES", True),
            patch.object(vd.threading.Thread, "start") as start,
        ):
            detector = vd.Detector(
                learn_resolution_templates=False,
                category_thresholds={},
                category_rois={},
                coarse_scale=1.0,
            )
            start.assert_not_called()

        template = self.template()
        with (
            patch.object(detector, "_templates_for", return_value={"synthetic": [template]}),
            patch.object(detector, "_queue_learn_candidate") as learn,
        ):
            detections = detector.detect(self.image, ["synthetic"])
            self.assertTrue(detections)
            detector.learn_action(self.image, detections[0])
            learn.assert_not_called()

        self.assertTrue(detector.stop())
        self.assertTrue(detector._learn_queue.empty())

    def test_learning_default_still_follows_config(self):
        with (
            patch.object(vd, "LEARN_RESOLUTION_TEMPLATES", True),
            patch.object(vd.threading.Thread, "start") as start,
        ):
            detector = vd.Detector()
            self.assertTrue(detector.learn_resolution_templates)
            start.assert_called_once()
            self.assertTrue(detector.stop())


class DetectorShutdownTests(unittest.TestCase):

    def test_stop_drains_pending_candidates_and_rejects_new_ones(self):
        processed = []
        with (
            patch.object(vd.Detector, "load_templates"),
            patch.object(vd.Detector, "_process_learn_candidate", side_effect=lambda *item: processed.append(item)),
            patch.object(cv2, "imread", side_effect=AssertionError("asset lido")),
        ):
            detector = vd.Detector()
            self.addCleanup(detector.stop)
            frame = np.zeros((8, 8, 3), dtype=np.uint8)
            template = {"category": "synthetic", "name": "item_001.png", "width": 4, "height": 4}
            for confidence in (0.9, 0.95):
                detector._queue_learn_candidate(frame, template, 0, 0, 8, 8, confidence)

            self.assertTrue(detector.stop())
            self.assertEqual(processed[-1][4], 0.95)
            self.assertIn([item[4] for item in processed], [[0.95], [0.9, 0.95]])
            self.assertEqual(detector._learn_pending, {})
            detector._queue_learn_candidate(frame, template, 0, 0, 8, 8, 1.0)
            self.assertTrue(detector.stop())
            self.assertTrue(detector._learn_queue.empty())
            self.assertEqual(detector._learn_queue.unfinished_tasks, 0)

    def test_failed_candidate_does_not_block_shutdown(self):
        with (
            patch.object(vd.Detector, "load_templates"),
            patch.object(vd.Detector, "_process_learn_candidate", side_effect=RuntimeError("falha simulada")),
            patch.object(vd.logger, "exception") as report,
        ):
            detector = vd.Detector()
            self.addCleanup(detector.stop)
            detector._learn_queue.put(("synthetic", "item_001.png", (8, 8), None, 0.9))
            self.assertTrue(detector.stop())
            report.assert_called_once()
            self.assertEqual(detector._learn_queue.unfinished_tasks, 0)


if __name__ == "__main__":
    unittest.main()

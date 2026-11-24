"""Regressões da seleção de recortes e da cobertura progressiva."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vision.detector import Detector  # noqa: E402
from vision.template_updater import DefaultTemplateScanner  # noqa: E402
from core.state_machine import StateMachine  # noqa: E402
from unittest.mock import Mock  # noqa: E402


class FidelityPolicyTests(unittest.TestCase):
    def setUp(self):
        self.loader = patch.object(Detector, "load_templates")
        self.loader.start()
        self.addCleanup(self.loader.stop)
        self.detector = Detector(threshold=0.8, category_thresholds={"box": 0.8},
                                 learn_resolution_templates=False)
        self.addCleanup(self.detector.stop)
        rng = np.random.default_rng(51)
        self.image = rng.integers(32, 224, (24, 24, 3), dtype=np.uint8)
        self.reference = self.detector._build_template("box", "item.png", self.image, None, "default")

    def test_validation_rejects_coverage_and_color_changes(self):
        perfect = self.detector._crop_fidelity(self.image, self.reference)
        self.assertGreater(perfect, 0.999)
        brighter = (self.image.astype(np.uint16) + 5).astype(np.uint8)
        self.assertLess(self.detector._crop_fidelity(brighter, self.reference), perfect)
        overlay = self.image.copy()
        overlay[:5] = 255
        self.assertIsNone(self.detector._crop_fidelity(overlay, self.reference))
        self.assertIsNone(self.detector._crop_fidelity(self.image // 2, self.reference))

    def test_existing_crop_wins_against_worse_candidate_even_if_metadata_is_wrong(self):
        old = self.detector._build_template("box", "item.png", self.image.copy(), None, "override")
        self.detector._resolution_templates[(100, 200)] = {"box": [old]}
        self.detector._fidelity[((100, 200), "box", "item.png")] = 0.1
        worse = self.image.copy()
        worse[0, 0] = 0
        with patch.object(self.detector, "_write_learn_candidate") as write:
            self.detector._process_learn_candidate("box", "item.png", (100, 200), worse,
                                                   0.99, 10, self.reference)
        write.assert_not_called()

    def test_mask_is_passed_to_the_new_override(self):
        mask = np.full(self.image.shape[:2], 255, dtype=np.uint8)
        mask[:, :4] = 0
        reference = self.detector._build_template("box", "item.png", self.image, mask, "default")
        with patch.object(self.detector, "_write_learn_candidate") as write:
            self.detector._process_learn_candidate("box", "item.png", (100, 200),
                                                   self.image.copy(), 1.0, 1, reference)
        np.testing.assert_array_equal(write.call_args.args[6], mask)

    def test_discovery_reaches_nonpriority_and_known_templates(self):
        self.detector.templates = [
            self.detector._build_template(category, f"item_{index}.png", self.image, None, "default")
            for category in ("box", "food") for index in range(8)
        ]
        self.detector._resolution_templates[(100, 200)] = {
            "food": [self.detector._build_template("food", f"item_{i}.png", self.image, None, "override")
                     for i in range(8)]
        }
        scanner = DefaultTemplateScanner(self.detector)
        self.addCleanup(scanner.stop)
        frame = np.zeros((200, 100, 3), dtype=np.uint8)
        with patch.object(scanner, "detect", return_value=[]):
            for _ in range(48):
                scanner.detect_discovery(frame, ["box"])
        self.assertEqual(scanner.discovery_progress, (16, 16))

    def test_partial_search_requests_full_scan_before_exploring(self):
        detector = Mock(spec=["request_full_scan"])
        machine = StateMachine(Mock(), detector=detector)
        detection = {"category": "food", "_search_incomplete": True}
        with (patch.object(machine, "_timed_out", return_value=False),
              patch.object(machine, "_apply_rules", return_value=False),
              patch.object(machine, "_explore_screen") as explore):
            machine.update([detection])
        detector.request_full_scan.assert_called_once_with()
        explore.assert_not_called()


if __name__ == "__main__":
    unittest.main()

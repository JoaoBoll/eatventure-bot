"""Valida o runner de regressão com arquivos e detector totalmente mockados."""

import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

import test_detection as regression


class DetectionScriptTests(unittest.TestCase):

    def setUp(self):
        self.images_dir = MagicMock()
        self.images_dir.glob.return_value = [Path("synthetic.png")]
        self.golden = MagicMock()
        self.golden.exists.return_value = True
        self.golden.read_text.return_value = json.dumps({"synthetic.png": []})
        self.detector = MagicMock()
        self.detector.templates = []
        self.detector.coarse_scale = 0.7
        self.detector.detect.return_value = []

        patches = (
            patch.object(regression, "IMAGES_DIR", self.images_dir),
            patch.object(regression, "GOLDEN_PATH", self.golden),
            patch.object(regression.log, "setup"),
            patch.object(regression, "Detector", return_value=self.detector),
            patch.object(regression.cv2, "imread", return_value=np.zeros((4, 4, 3), dtype=np.uint8)),
        )
        mocks = []
        for item in patches:
            mocks.append(item.start())
            self.addCleanup(item.stop)
        self.constructor = mocks[-2]
        self.imread = mocks[-1]

    def run_script(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = regression.main(list(args))
        return code, output.getvalue()

    def test_success_disables_learning_and_stops_detector(self):
        code, output = self.run_script()
        self.assertEqual(code, 0, output)
        self.constructor.assert_called_once_with(learn_resolution_templates=False)
        self.detector.stop.assert_called_once()

    def test_missing_images_fail_in_all_modes_without_writing_golden(self):
        self.images_dir.glob.return_value = []
        for args in ((), ("--bench",), ("--update",)):
            with self.subTest(args=args):
                code, output = self.run_script(*args)
                self.assertEqual(code, 1, output)
                self.assertIn("Nenhuma imagem PNG", output)
        self.imread.assert_not_called()
        self.detector.detect.assert_not_called()
        self.golden.write_text.assert_not_called()

    def test_unreadable_images_fail_in_all_modes_without_writing_golden(self):
        self.imread.return_value = None
        for args in ((), ("--bench",), ("--update",)):
            with self.subTest(args=args):
                code, output = self.run_script(*args)
                self.assertEqual(code, 1, output)
                self.assertIn("Não carregou: synthetic.png", output)
        self.detector.detect.assert_not_called()
        self.golden.write_text.assert_not_called()

    def test_benchmark_warms_up_and_runs_three_rounds(self):
        with patch.object(regression.time, "perf_counter", side_effect=(0.0, 1.0)):
            code, output = self.run_script("--bench")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.detector.detect.call_count, 4)
        self.detector.stop.assert_called_once()

    def test_missing_expected_image_fails_even_with_no_expected_detections(self):
        failures, warnings = regression.compare({"missing.png": []}, {})
        self.assertEqual(failures, ["missing.png: imagem esperada ausente."])
        self.assertEqual(warnings, [])

    def test_detector_stops_when_detection_raises(self):
        self.detector.detect.side_effect = RuntimeError("falha simulada")
        with self.assertRaisesRegex(RuntimeError, "falha simulada"):
            self.run_script()
        self.detector.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()

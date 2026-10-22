"""Atualizacao em segundo plano com frames sinteticos e persistencia simulada."""

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vision import detector as vd  # noqa: E402


class TemplateWorkerTests(unittest.TestCase):

    def setUp(self):
        for item in (
            patch.object(vd.Detector, "load_templates"),
            patch.object(vd.Detector, "_save_fidelity"),
            patch.object(vd.Detector, "_reassina_overrides"),
            patch.object(cv2, "imread", side_effect=AssertionError("asset lido")),
            patch.object(vd, "TEMPLATE_REFRESH_INTERVAL", 0),
        ):
            item.start()
            self.addCleanup(item.stop)

        self.key = (8, 8)
        self.frame = np.ones((8, 8, 3), dtype=np.uint8)
        self.detector = vd.Detector(
            category_thresholds={"box": 0.95},
            learn_resolution_templates=False,
        )
        self.addCleanup(self.detector.stop)
        self.detector.learn_resolution_templates = True
        self.detector._resolution_templates[self.key] = {
            "box": [{"name": "item_001.png"}]
        }

    def test_pending_refresh_keeps_only_a_copy_of_latest_frame(self):
        first_sequence = self.detector._queue_refresh_frame(self.frame)
        self.frame.fill(2)
        latest_sequence = self.detector._queue_refresh_frame(self.frame)
        self.frame.fill(3)

        pending, sequence = self.detector._refresh_frame
        np.testing.assert_array_equal(pending, np.full_like(self.frame, 2))
        self.assertGreater(latest_sequence, first_sequence)
        self.assertEqual(sequence, latest_sequence)
        self.assertTrue(self.detector._refresh_pending)
        self.assertEqual(self.detector._learn_queue.qsize(), 1)

    def test_disabled_learning_does_not_schedule_refresh(self):
        self.detector.learn_resolution_templates = False

        sequence = self.detector._queue_refresh_frame(self.frame)

        self.assertIsNone(sequence)
        self.assertIsNone(self.detector._refresh_frame)
        self.assertFalse(self.detector._refresh_pending)
        self.assertTrue(self.detector._learn_queue.empty())

    def test_action_only_learning_still_refreshes_already_covered_templates(self):
        self.detector.learn_only_actions = True

        sequence = self.detector._queue_refresh_frame(self.frame)

        self.assertIsInstance(sequence, int)
        self.assertEqual(self.detector._refresh_frame[1], sequence)
        np.testing.assert_array_equal(self.detector._refresh_frame[0], self.frame)
        self.assertTrue(self.detector._refresh_pending)
        self.assertEqual(self.detector._learn_queue.qsize(), 1)

    def test_uncovered_resolution_still_orders_foreground_generations(self):
        self.detector._resolution_templates = {(16, 16): {"box": [{}]}}

        first_sequence = self.detector._queue_refresh_frame(self.frame)
        next_sequence = self.detector._queue_refresh_frame(self.frame)

        self.assertIsInstance(first_sequence, int)
        self.assertGreater(next_sequence, first_sequence)
        self.assertIsNone(self.detector._refresh_frame)
        self.assertFalse(self.detector._refresh_pending)
        self.assertTrue(self.detector._learn_queue.empty())

    def test_refresh_interval_limits_scans_without_losing_generation_order(self):
        self.detector._refresh_started_at = 10.0
        with (
            patch.object(vd, "TEMPLATE_REFRESH_INTERVAL", 1.0),
            patch.object(vd.time, "monotonic", side_effect=(10.5, 11.0)),
        ):
            skipped_sequence = self.detector._queue_refresh_frame(self.frame)
            self.assertTrue(self.detector._learn_queue.empty())
            self.assertIsNone(self.detector._refresh_frame)

            scheduled_sequence = self.detector._queue_refresh_frame(self.frame)

        self.assertGreater(scheduled_sequence, skipped_sequence)
        self.assertEqual(self.detector._learn_queue.qsize(), 1)
        self.assertTrue(self.detector._refresh_pending)
        self.assertEqual(self.detector._refresh_frame[1], scheduled_sequence)

    def test_scanning_uses_worker_and_new_frames_do_not_wait_for_it(self):
        entered = threading.Event()
        release = threading.Event()
        queued = threading.Event()
        completed = threading.Event()
        seen = []

        def refresh(frame, sequence):
            seen.append((frame.copy(), sequence, threading.get_ident()))
            if len(seen) == 1:
                entered.set()
                release.wait(2)
            else:
                completed.set()

        def queue_latest():
            self.detector._queue_refresh_frame(np.full_like(self.frame, 2))
            self.detector._queue_refresh_frame(np.full_like(self.frame, 3))
            queued.set()

        self.detector._refresh_defaults = Mock(side_effect=refresh)
        self.detector._learn_thread.start()
        caller = threading.Thread(target=queue_latest, daemon=True)
        try:
            first_sequence = self.detector._queue_refresh_frame(self.frame)
            self.assertTrue(entered.wait(1), "worker nao iniciou a busca")
            caller.start()
            self.assertTrue(queued.wait(1), "busca bloqueou a entrega de frames")
            self.assertEqual(self.detector._learn_queue.qsize(), 1)
        finally:
            release.set()
            if caller.ident is not None:
                caller.join(2)

        self.assertTrue(completed.wait(1), "worker nao consumiu o frame recente")
        self.assertTrue(self.detector.stop())
        self.assertEqual(len(seen), 2)
        np.testing.assert_array_equal(seen[0][0], self.frame)
        np.testing.assert_array_equal(seen[1][0], np.full_like(self.frame, 3))
        self.assertEqual(seen[0][1], first_sequence)
        self.assertGreater(seen[1][1], first_sequence)
        self.assertTrue(all(item[2] != threading.get_ident() for item in seen))
        self.assertTrue(all(item[2] == self.detector._learn_thread.ident for item in seen))

    def test_stop_cancels_pending_scan_and_drains_already_queued_candidates(self):
        entered = threading.Event()
        release = threading.Event()
        processed = []

        def process(*candidate):
            processed.append(candidate)
            if len(processed) == 1:
                entered.set()
                release.wait(2)

        candidates = [
            ("box", "item_001.png", self.key, self.frame, 0.95, 1),
            ("box", "item_002.png", self.key, self.frame, 0.96, 2),
        ]
        self.detector._process_learn_candidate = Mock(side_effect=process)
        self.detector._refresh_defaults = Mock()
        self.detector._learn_queue.put_nowait(candidates[0])
        self.detector._learn_thread.start()
        try:
            self.assertTrue(entered.wait(1), "worker nao iniciou a candidata")
            self.detector._queue_refresh_frame(self.frame)
            self.detector._learn_queue.put_nowait(candidates[1])
            with patch.object(vd.logger, "warning"):
                self.assertFalse(self.detector.stop(timeout=0.01))
            self.assertIsNone(self.detector._refresh_frame)
            self.assertFalse(self.detector._refresh_pending)
        finally:
            release.set()

        self.assertTrue(self.detector.stop())
        self.assertEqual([item[1] for item in processed], [item[1] for item in candidates])
        self.detector._refresh_defaults.assert_not_called()
        self.detector._queue_refresh_frame(self.frame)
        self.assertTrue(self.detector._learn_queue.empty())
        self.assertEqual(self.detector._learn_queue.unfinished_tasks, 0)

    def test_failed_scan_does_not_kill_worker_or_block_next_refresh(self):
        failed = threading.Event()
        completed = threading.Event()
        calls = []

        def refresh(frame, sequence):
            calls.append(sequence)
            if len(calls) == 1:
                raise RuntimeError("falha sintetica")
            completed.set()

        self.detector._refresh_defaults = Mock(side_effect=refresh)
        with patch.object(vd.logger, "exception", side_effect=lambda *args: failed.set()) as report:
            self.detector._learn_thread.start()
            self.detector._queue_refresh_frame(self.frame)
            self.assertTrue(failed.wait(1), "falha da busca nao foi registrada")
            self.detector._queue_refresh_frame(self.frame)
            self.assertTrue(completed.wait(1), "worker parou apos a falha")
            self.assertTrue(self.detector.stop())

        report.assert_called_once()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.detector._learn_queue.unfinished_tasks, 0)

    def test_old_background_result_cannot_replace_newer_foreground_generation(self):
        entered = threading.Event()
        release = threading.Event()
        enqueued = threading.Event()
        newer_crop = np.full_like(self.frame, 7)

        def refresh(frame, sequence):
            entered.set()
            release.wait(2)
            self.detector._learn_queue.put_nowait(
                ("box", "item_001.png", self.key, frame, 1.0, sequence)
            )
            enqueued.set()

        self.detector._refresh_defaults = Mock(side_effect=refresh)
        with (
            patch.object(Path, "mkdir"),
            patch.object(cv2, "imwrite", return_value=True) as write,
        ):
            self.detector._learn_thread.start()
            try:
                old_sequence = self.detector._queue_refresh_frame(self.frame)
                self.assertTrue(entered.wait(1), "worker nao iniciou a busca")
                self.detector._learn_queue.put_nowait(
                    ("box", "item_001.png", self.key, newer_crop, 0.95, old_sequence + 1)
                )
            finally:
                release.set()

            self.assertTrue(enqueued.wait(1), "busca nao entregou a candidata antiga")
            self.assertTrue(self.detector.stop())

        write.assert_called_once()
        current = self.detector._resolution_templates[self.key]["box"][0]
        np.testing.assert_array_equal(current["image"], newer_crop)
        self.assertEqual(self.detector._fidelity[(self.key, "box", "item_001.png")], 0.95)
        self.assertEqual(self.detector._learn_queue.unfinished_tasks, 0)

    def test_real_background_scanner_updates_covered_template_from_synthetic_frame(self):
        image = np.random.default_rng(42).integers(
            0, 256, (12, 10, 3), dtype=np.uint8
        )
        frame = np.zeros((240, 108, 3), dtype=np.uint8)
        frame[101:113, 31:41] = image
        key = (108, 240)
        written = threading.Event()

        def save(*args):
            written.set()
            return True

        with patch.multiple(
            vd,
            REFERENCE_WIDTH=108,
            REFERENCE_HEIGHT=240,
            TEMPLATE_SCALE_BASIS="short_side",
            TEMPLATE_SCALE_STEPS=(1.0,),
            DETECTOR_DEBUG_MISSES=False,
            CATEGORY_COLOR_THRESHOLDS={},
        ):
            self.detector.threshold = 0.999
            self.detector.color_threshold = 0.999
            self.detector.category_thresholds = {"box": 0.995}
            self.detector.category_rois = {"box": (0.0, 0.0, 1.0, 1.0)}
            self.detector.coarse_scale = 1.0
            self.detector.templates = [
                self.detector._build_template(
                    "box", "108x240_item_001.png", image, None, "default"
                )
            ]
            previous = self.detector._build_template(
                "box", "item_001.png", np.zeros_like(image), None, "override"
            )
            self.detector._resolution_templates = {key: {"box": [previous]}}

            with (
                patch.object(Path, "mkdir"),
                patch.object(cv2, "imwrite", side_effect=save) as write,
            ):
                self.detector._learn_thread.start()
                sequence = self.detector._queue_refresh_frame(frame)
                self.assertTrue(written.wait(2), "scanner nao gerou a atualizacao")
                self.assertTrue(self.detector.stop())

        self.assertIsNotNone(self.detector._refresh_scanner)
        write.assert_called_once()
        current = self.detector._resolution_templates[key]["box"][0]
        self.assertIsNot(current, previous)
        self.assertEqual(current["origin"], "override")
        np.testing.assert_array_equal(current["image"], image)
        self.assertGreaterEqual(self.detector._fidelity[(key, "box", "item_001.png")], 0.995)
        self.assertEqual(self.detector._learned_sequence[(key, "box", "item_001.png")], sequence)
        self.assertEqual(self.detector._learn_queue.unfinished_tasks, 0)


if __name__ == "__main__":
    unittest.main()

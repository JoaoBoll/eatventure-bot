"""Coalescencia de candidatas com pixels sinteticos e persistencia simulada."""

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


class TemplateQueueTests(unittest.TestCase):

    def setUp(self):
        for item in (
            patch.object(vd.Detector, "load_templates"),
            patch.object(cv2, "imread", side_effect=AssertionError("asset lido")),
        ):
            item.start()
            self.addCleanup(item.stop)
        self.detector = vd.Detector(
            threshold=0.90,
            category_thresholds={"box": 0.95},
            learn_resolution_templates=False,
        )
        self.addCleanup(self.detector.stop)
        self.frame = np.ones((8, 8, 3), dtype=np.uint8)
        self.template = {
            "category": "box", "name": "8x8_item_001.png", "width": 4, "height": 4,
        }
        self.identity = ((8, 8), "box", "item_001.png")

    def queue(self, confidence=0.95, sequence=None, template=None, key=(8, 8)):
        self.detector._queue_learn_candidate(
            self.frame, self.template if template is None else template,
            0, 0, *key, confidence, sequence,
        )

    def test_identical_pending_crop_is_reused_and_latest_lower_score_wins(self):
        self.queue(1.0, 1)
        first = self.detector._learn_pending[self.identity]

        for sequence in range(2, 102):
            self.queue(0.95, sequence)

        latest = self.detector._learn_pending[self.identity]
        self.assertEqual(self.detector._learn_queue.qsize(), 1)
        self.assertEqual(self.detector._learn_queue.unfinished_tasks, 1)
        self.assertIs(latest[3], first[3])
        self.assertEqual(latest[4:], (0.95, 101))

    def test_changed_crop_is_copied_and_isolated_from_later_frame_changes(self):
        self.queue(sequence=1)
        first = self.detector._learn_pending[self.identity][3]
        self.frame.fill(2)
        self.queue(sequence=2)
        latest = self.detector._learn_pending[self.identity][3]
        self.frame.fill(3)

        self.assertIsNot(first, latest)
        np.testing.assert_array_equal(first, np.ones_like(first))
        np.testing.assert_array_equal(latest, np.full_like(latest, 2))
        self.assertEqual(self.detector._learn_queue.qsize(), 1)

    def test_older_sequence_is_ignored_and_equal_sequence_uses_last_candidate(self):
        self.queue(1.0, 20)
        pending = self.detector._learn_pending[self.identity]
        self.frame.fill(2)
        self.queue(0.95, 19)
        self.assertIs(self.detector._learn_pending[self.identity], pending)

        self.queue(0.96, 20)
        latest = self.detector._learn_pending[self.identity]
        self.assertEqual(latest[4:], (0.96, 20))
        np.testing.assert_array_equal(latest[3], np.full((4, 4, 3), 2, dtype=np.uint8))

    def test_implicit_sequence_follows_explicit_sequence(self):
        self.queue(sequence=20)
        self.queue(0.96)

        self.assertEqual(self.detector._learn_pending[self.identity][4:], (0.96, 21))

    def test_identity_groups_base_name_but_separates_resolution_and_category(self):
        self.queue()
        self.queue(template={**self.template, "name": "16x16_item_001.png"})
        self.queue(key=(16, 16))
        self.queue(template={**self.template, "category": "synthetic"})
        self.queue(template={**self.template, "name": "item_002.png"})

        self.assertEqual(self.detector._learn_queue.qsize(), 4)
        self.assertEqual(len(self.detector._learn_pending), 4)

    def test_stop_drains_latest_pending_candidates_and_direct_jobs(self):
        self.queue(1.0, 1)
        self.frame.fill(2)
        self.queue(0.95, 2)
        direct = ("box", "item_002.png", (8, 8), self.frame.copy(), 0.96)
        self.detector._learn_queue.put_nowait(direct)
        self.detector._process_learn_candidate = Mock()
        self.detector._learn_thread.start()

        self.assertTrue(self.detector.stop())

        calls = self.detector._process_learn_candidate.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[4:], (0.95, 2))
        np.testing.assert_array_equal(calls[0].args[3], np.full((4, 4, 3), 2, dtype=np.uint8))
        self.assertEqual(calls[1].args, direct)
        self.assertEqual(self.detector._learn_pending, {})
        self.assertEqual(self.detector._learn_queue.unfinished_tasks, 0)
        self.queue(sequence=3)
        self.assertTrue(self.detector._learn_queue.empty())
        self.assertEqual(self.detector._learn_pending, {})

    def test_new_pending_crop_can_follow_one_already_being_processed(self):
        entered = threading.Event()
        release = threading.Event()
        processed = []

        def process(*candidate):
            processed.append(candidate)
            if len(processed) == 1:
                entered.set()
                release.wait(2)

        self.detector._process_learn_candidate = Mock(side_effect=process)
        self.queue(sequence=1)
        self.detector._learn_thread.start()
        try:
            self.assertTrue(entered.wait(1), "worker nao iniciou candidata")
            self.frame.fill(2)
            self.queue(0.98, 2)
            self.frame.fill(3)
            self.queue(0.95, 3)
            self.assertEqual(self.detector._learn_queue.qsize(), 1)
        finally:
            release.set()

        self.assertTrue(self.detector.stop())
        self.assertEqual([item[5] for item in processed], [1, 3])
        np.testing.assert_array_equal(processed[-1][3], np.full((4, 4, 3), 3, dtype=np.uint8))
        self.assertEqual(self.detector._learn_pending, {})
        self.assertEqual(self.detector._learn_queue.unfinished_tasks, 0)


if __name__ == "__main__":
    unittest.main()

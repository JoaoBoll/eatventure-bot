"""Prévia validada com relógio, encoder e quadros simulados."""

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.preview import FramePreview, preview_paths
from core.runtime import RuntimeControl


class WebPreviewTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.status = self.root / "phone.status.json"
        self.preview = FramePreview(self.status, "session-123")
        self.frame = object()
        wall = patch("core.preview.time.time", return_value=1000.0)
        monotonic = patch("core.preview.time.monotonic", return_value=10.0)
        self.wall = wall.start()
        self.monotonic = monotonic.start()
        self.addCleanup(wall.stop)
        self.addCleanup(monotonic.stop)

    def request(self, **values):
        request = {"session_id": "session-123", "expires_at": 1008.0, **values}
        self.preview.paths["request"].write_text(json.dumps(request), encoding="utf-8")

    def metadata(self):
        return json.loads(self.preview.paths["metadata"].read_text(encoding="utf-8"))

    def test_paths_and_runtime_integration(self):
        paths = preview_paths(self.status)
        self.assertEqual(paths, {
            "request": self.root / "phone.vision.request.json",
            "image": self.root / "phone.vision.jpg",
            "metadata": self.root / "phone.vision.meta.json",
        })
        runtime = RuntimeControl("phone", self.status, self.root / "stop", "session-123")
        self.assertEqual(runtime.preview.paths, paths)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_disabled_preview_does_not_encode_or_create_files(self):
        with patch("core.preview._encode_jpeg") as encode:
            self.assertFalse(self.preview.requested())
            self.assertFalse(self.preview.due())
            self.assertFalse(self.preview.publish(self.frame))
        encode.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_request_is_cached_for_a_quarter_second(self):
        self.request()
        self.assertTrue(self.preview.requested())
        self.preview.paths["request"].unlink()
        self.monotonic.return_value = 10.2
        self.assertTrue(self.preview.requested())
        self.monotonic.return_value = 10.26
        self.assertFalse(self.preview.requested())

    def test_invalid_stale_or_excessive_leases_are_rejected(self):
        requests = [
            {"session_id": "other", "expires_at": 1008},
            {"session_id": "session-123", "expires_at": 999},
            {"session_id": "session-123", "expires_at": 1016},
            {"session_id": "session-123", "expires_at": "invalid"},
            {"session_id": "session-123", "expires_at": float("inf")},
            [],
        ]
        for index, value in enumerate(requests):
            with self.subTest(request=value):
                self.preview.paths["request"].write_text(json.dumps(value), encoding="utf-8")
                self.monotonic.return_value = 10.0 + index
                self.assertFalse(self.preview.requested())
        self.preview.paths["request"].write_text("x" * 4097, encoding="utf-8")
        self.monotonic.return_value = 20.0
        self.assertFalse(self.preview.requested())

    def test_expired_cached_lease_stops_immediately(self):
        self.request(expires_at=1000.1)
        self.assertTrue(self.preview.requested())
        self.wall.return_value = 1000.2
        self.monotonic.return_value = 10.1
        self.assertFalse(self.preview.requested())

    def test_jpeg_quality_and_atomic_metadata(self):
        self.request()
        encoder = Mock(return_value=(True, SimpleNamespace(tobytes=lambda: b"simulated-frame")))
        cv2 = SimpleNamespace(imencode=encoder, IMWRITE_JPEG_QUALITY=1)
        original_replace = os.replace
        replacements = []

        def replace(source, destination):
            self.assertEqual(Path(source).parent, self.root)
            self.assertTrue(Path(source).is_file())
            replacements.append(Path(destination))
            original_replace(source, destination)

        with patch.dict(sys.modules, {"cv2": cv2}), patch("core.preview.os.getpid", return_value=4321), patch("core.preview.os.replace", side_effect=replace):
            self.assertTrue(self.preview.publish(self.frame))
        encoder.assert_called_once_with(".jpg", self.frame, [1, 75])
        self.assertEqual(replacements, [self.preview.paths["image"], self.preview.paths["metadata"]])
        self.assertEqual(self.metadata(), {"session_id": "session-123", "pid": 4321, "updated_at": 1000.0})
        self.assertEqual(self.preview.paths["image"].read_bytes(), b"simulated-frame")
        self.assertFalse(list(self.root.glob(".vision-*")))

    def test_publication_is_limited_to_twenty_frames_per_second(self):
        self.request()
        with patch("core.preview._encode_jpeg", return_value=b"dummy") as encode:
            self.assertTrue(self.preview.due())
            self.assertTrue(self.preview.publish(self.frame))
            self.monotonic.return_value = 10.049
            self.assertFalse(self.preview.due())
            self.assertFalse(self.preview.publish(self.frame))
            self.monotonic.return_value = 10.051
            self.assertTrue(self.preview.publish(self.frame))
        self.assertEqual(encode.call_count, 2)

    def test_disabled_preview_removes_own_output_without_encoding(self):
        self.request()
        with patch("core.preview._encode_jpeg", return_value=b"dummy"):
            self.assertTrue(self.preview.publish(self.frame))
        self.preview.paths["request"].unlink()
        self.monotonic.return_value = 11.0
        with patch("core.preview._encode_jpeg") as encode:
            self.assertFalse(self.preview.publish(self.frame))
        encode.assert_not_called()
        self.assertFalse(self.preview.paths["image"].exists())
        self.assertFalse(self.preview.paths["metadata"].exists())

    def test_io_failures_do_not_escape_or_leave_temporary_files(self):
        self.request()
        with patch("core.preview._encode_jpeg", return_value=b"dummy"), patch("core.preview.os.replace", side_effect=PermissionError("occupied")):
            self.assertFalse(self.preview.publish(self.frame))
        self.assertFalse(self.preview.paths["image"].exists())
        self.assertFalse(self.preview.paths["metadata"].exists())
        self.assertFalse(list(self.root.glob(".vision-*")))

    def test_encoding_failure_is_contained_and_rate_limited(self):
        self.request()
        with patch("core.preview._encode_jpeg", side_effect=RuntimeError("bad frame")) as encode:
            self.assertFalse(self.preview.publish(self.frame))
            self.monotonic.return_value = 10.025
            self.assertFalse(self.preview.publish(self.frame))
        encode.assert_called_once()
        self.assertFalse(self.preview.paths["image"].exists())

    def test_clear_removes_only_current_sessions_output(self):
        self.request()
        with patch("core.preview._encode_jpeg", return_value=b"dummy"):
            self.assertTrue(self.preview.publish(self.frame))
        self.preview.paths["metadata"].write_text(json.dumps({"session_id": "another"}), encoding="utf-8")
        self.preview.clear()
        self.assertTrue(self.preview.paths["image"].exists())
        self.assertEqual(self.metadata()["session_id"], "another")
        self.preview.paths["metadata"].write_text(json.dumps({"session_id": "session-123"}), encoding="utf-8")
        self.preview.clear()
        self.assertFalse(self.preview.paths["image"].exists())
        self.assertFalse(self.preview.paths["metadata"].exists())
        self.assertTrue(self.preview.paths["request"].exists())

    def test_clear_handles_partial_publication(self):
        self.request()
        original_replace = os.replace

        def replace(source, destination):
            if Path(destination) == self.preview.paths["metadata"]:
                raise PermissionError("occupied")
            original_replace(source, destination)

        with patch("core.preview._encode_jpeg", return_value=b"dummy"), patch("core.preview.os.replace", side_effect=replace):
            self.assertFalse(self.preview.publish(self.frame))
        self.assertTrue(self.preview.paths["image"].exists())
        self.preview.clear()
        self.assertFalse(self.preview.paths["image"].exists())


class AsyncWebPreviewTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.preview = FramePreview(self.root / "phone.status.json", "session-123")
        encoder = patch("core.preview._encode_jpeg", return_value=b"simulatedJPEG")
        self.encode = encoder.start()
        self.addCleanup(encoder.stop)
        self.addCleanup(self.preview.stop)

    def request(self, duration=8):
        self.preview.paths["request"].write_text(json.dumps({
            "session_id": "session-123", "expires_at": time.time() + duration,
        }), encoding="utf-8")
        self.assertTrue(self.preview.requested())

    def wait_for(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        wake = threading.Event()
        while time.monotonic() < deadline:
            if predicate():
                return True
            wake.wait(0.01)
        return predicate()

    def test_disabled_submit_does_not_start_a_thread_or_touch_disk(self):
        with patch("core.preview.threading.Thread") as thread, patch("core.preview._read_json") as read:
            self.assertFalse(self.preview.submit(object(), Mock()))
        thread.assert_not_called()
        read.assert_not_called()
        self.encode.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_render_and_encode_run_in_worker_and_latest_pending_frame_wins(self):
        self.request()
        first, second, latest = object(), object(), object()
        entered = threading.Event()
        release = threading.Event()
        completed = threading.Event()
        rendered, encoded, thread_ids = [], [], []

        def render(frame):
            rendered.append(frame)
            thread_ids.append(threading.get_ident())
            if frame is first:
                entered.set()
                release.wait(timeout=2)
            return frame

        def encode(frame):
            encoded.append(frame)
            thread_ids.append(threading.get_ident())
            if frame is latest:
                completed.set()
            return b"simulatedJPEG"

        self.encode.side_effect = encode
        self.assertTrue(self.preview.submit(first, render))
        self.assertTrue(entered.wait(timeout=1))
        try:
            self.assertTrue(self.preview.submit(second, render))
            self.assertTrue(self.preview.submit(latest, render))
            self.assertFalse(completed.is_set())
        finally:
            release.set()
        self.assertTrue(completed.wait(timeout=2))
        self.assertEqual(rendered, [first, latest])
        self.assertEqual(encoded, [first, latest])
        self.assertEqual(len(set(thread_ids)), 1)
        self.assertNotEqual(thread_ids[0], threading.get_ident())

    def test_idle_worker_expires_lease_and_removes_snapshot(self):
        self.request(duration=0.6)
        self.assertTrue(self.preview.submit(object()))
        self.assertTrue(self.wait_for(self.preview.paths["metadata"].exists))
        self.assertTrue(self.wait_for(lambda: not self.preview.paths["metadata"].exists()))
        self.assertFalse(self.preview.paths["image"].exists())
        self.encode.assert_called_once()

    def test_requested_does_not_wait_for_render_and_stop_is_final(self):
        self.request()
        entered = threading.Event()
        release = threading.Event()

        def render(frame):
            entered.set()
            release.wait(timeout=2)
            return frame

        self.assertTrue(self.preview.submit(object(), render))
        self.assertTrue(entered.wait(timeout=1))
        self.preview.paths["request"].unlink()
        with self.preview._condition:
            self.preview._checked_at = float("-inf")
        self.assertFalse(self.preview.requested())
        release.set()
        self.preview.stop()
        self.preview.stop()
        self.assertFalse(self.preview._thread.is_alive())
        self.assertFalse(self.preview.start())
        self.assertFalse(self.preview.submit(object(), render))
        self.encode.assert_not_called()
        self.assertFalse(self.preview.paths["metadata"].exists())

    def test_render_failure_keeps_worker_alive_and_warning_is_limited(self):
        self.request()
        first_failed = threading.Event()
        second_failed = threading.Event()
        recovered = threading.Event()
        failures = [first_failed, second_failed]

        def render(frame):
            if failures:
                failures.pop(0).set()
                raise ValueError("simulated rendering failure")
            return frame

        def encode(frame):
            recovered.set()
            return b"simulatedJPEG"

        self.encode.side_effect = encode
        with patch("core.preview.logger.warning") as warning:
            self.assertTrue(self.preview.submit(object(), render))
            self.assertTrue(first_failed.wait(timeout=1))
            self.assertTrue(self.preview.submit(object(), render))
            self.assertTrue(second_failed.wait(timeout=1))
            self.assertTrue(self.preview.submit(object(), render))
            self.assertTrue(recovered.wait(timeout=1))
            self.assertTrue(self.preview._thread.is_alive())
            warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()

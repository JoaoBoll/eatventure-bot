"""Stream multipart com bytes fictícios, sem servidor, ADB ou imagens reais."""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

try:
    import httpx
    from fastapi.testclient import TestClient
except ImportError as error:
    raise unittest.SkipTest("Testes do stream precisam de fastapi e httpx.") from error

from core.device_profiles import ProfileStore
from core.preview import preview_paths
from dashboard.server import create_app, vision_stream_frames
from dashboard.supervisor import BotSupervisor, _Run


class SupervisorStreamTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = ProfileStore(self.root / "devices")
        self.store.save("phone", {"name": "Cozinha"})
        self.devices = SimpleNamespace(last_error=None, list=Mock(side_effect=AssertionError("ADB proibido.")))
        self.supervisor = BotSupervisor(self.root, store=self.store, devices=self.devices)
        self.process = Mock(pid=111)
        self.process.poll.return_value = None
        self.run = _Run(self.process, 990, "session-123", "usb-1", self.root / "phone.status.json", self.root / "phone.stop", worker_pid=222)
        self.supervisor._runs["phone"] = self.run
        self.paths = preview_paths(self.run.status_file)
        for item in (
            patch("dashboard.supervisor.time.time", return_value=1000),
            patch("dashboard.supervisor.subprocess.run", side_effect=AssertionError("Comando proibido.")),
            patch("dashboard.supervisor.subprocess.Popen", side_effect=AssertionError("Processo proibido.")),
        ):
            item.start()
            self.addCleanup(item.stop)

    def frame(self, content=b"simulatedJPEG", **metadata):
        self.paths["image"].write_bytes(content)
        self.paths["metadata"].write_text(json.dumps({
            "session_id": "session-123", "pid": 222, "updated_at": 1000, **metadata,
        }), encoding="utf-8")

    def test_open_validates_process_and_renews_lease_without_adb(self):
        self.assertEqual(self.supervisor.open_vision_stream("usb-1"), {"device_id": "phone", "session_id": "session-123"})
        lease = json.loads(self.paths["request"].read_text(encoding="utf-8"))
        self.assertEqual(lease, {"session_id": "session-123", "expires_at": 1008})
        self.devices.list.assert_not_called()
        self.process.poll.return_value = 0
        with self.assertRaises(RuntimeError):
            self.supervisor.open_vision_stream("phone")
        with self.assertRaises(KeyError):
            self.supervisor.open_vision_stream("unknown")
        self.devices.list.assert_not_called()

    def test_pending_and_identical_image_wait_instead_of_repeating_bytes(self):
        self.assertIsNone(self.supervisor.next_vision_frame("phone", "session-123"))
        self.frame()
        first = self.supervisor.next_vision_frame("phone", "session-123")
        self.assertEqual(first["content"], b"simulatedJPEG")
        self.assertIsNone(self.supervisor.next_vision_frame("phone", "session-123", first["signature"]))
        metadata = json.loads(self.paths["metadata"].read_text(encoding="utf-8"))
        metadata["updated_at"] = 1000.1
        self.paths["metadata"].write_text(json.dumps(metadata), encoding="utf-8")
        self.assertIsNone(self.supervisor.next_vision_frame("phone", "session-123", first["signature"]))
        self.frame(b"latest-simulatedJPEG", updated_at=1000.2)
        latest = self.supervisor.next_vision_frame("phone", "session-123", first["signature"], renew=True)
        self.assertEqual(latest["content"], b"latest-simulatedJPEG")
        self.devices.list.assert_not_called()

    def test_stale_or_wrong_metadata_is_not_streamed(self):
        for metadata in ({"pid": 999}, {"session_id": "old"}, {"updated_at": 980}):
            with self.subTest(metadata=metadata):
                self.frame(**metadata)
                self.assertIsNone(self.supervisor.next_vision_frame("phone", "session-123"))

    def test_stream_ends_when_process_exits_or_session_changes(self):
        self.process.poll.return_value = 0
        with self.assertRaises(RuntimeError):
            self.supervisor.next_vision_frame("phone", "session-123")
        self.process.poll.return_value = None
        self.run.session_id = "new-session"
        with self.assertRaises(RuntimeError):
            self.supervisor.next_vision_frame("phone", "session-123")
        self.devices.list.assert_not_called()


class StreamGeneratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_generator_skips_duplicates_renews_lease_and_emits_content_length(self):
        first = {"signature": (1,), "content": b"firstJPEG"}
        second = {"signature": (2,), "content": b"latestJPEG"}
        supervisor = SimpleNamespace(next_vision_frame=Mock(side_effect=[first, first, second, RuntimeError("ended")]))
        request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
        clock = {"value": 0.0}
        delays = []

        async def sleep(delay):
            delays.append(delay)
            clock["value"] += 1.1

        async def in_thread(function, *args):
            return function(*args)

        with (
            patch("dashboard.server.time", SimpleNamespace(monotonic=lambda: clock["value"])),
            patch("dashboard.server.asyncio", SimpleNamespace(sleep=sleep)),
            patch("starlette.concurrency.run_in_threadpool", new=in_thread),
        ):
            chunks = [chunk async for chunk in vision_stream_frames(request, supervisor, "phone", "session")]
        body = b"".join(chunks)
        self.assertEqual(body.count(b"--frame\r\n"), 2)
        self.assertEqual(body.count(b"firstJPEG"), 1)
        self.assertEqual(body.count(b"latestJPEG"), 1)
        self.assertIn(b"Content-Length: 9\r\n\r\nfirstJPEG\r\n", body)
        self.assertIn(b"Content-Length: 10\r\n\r\nlatestJPEG\r\n", body)
        self.assertEqual([call.args[-1] for call in supervisor.next_vision_frame.call_args_list], [False, False, True, False])
        self.assertTrue(all(delay >= 0.05 - 1e-9 for delay in delays))

    async def test_disconnect_exits_without_reading_files(self):
        supervisor = SimpleNamespace(next_vision_frame=Mock())
        request = SimpleNamespace(is_disconnected=AsyncMock(return_value=True))
        chunks = [chunk async for chunk in vision_stream_frames(request, supervisor, "phone", "session")]
        self.assertEqual(chunks, [])
        supervisor.next_vision_frame.assert_not_called()

    async def test_cancellation_propagates_without_retry(self):
        async def cancelled(*args):
            raise asyncio.CancelledError()

        supervisor = SimpleNamespace(next_vision_frame=Mock())
        request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
        with patch("starlette.concurrency.run_in_threadpool", new=cancelled):
            with self.assertRaises(asyncio.CancelledError):
                await anext(vision_stream_frames(request, supervisor, "phone", "session"))


class StreamApiTests(unittest.TestCase):
    def test_stream_auth_headers_and_validation_before_response(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = "simulated-stream-access-token-123456"
            supervisor = SimpleNamespace(
                store=ProfileStore(root / "devices"),
                shutdown=Mock(),
                open_vision_stream=Mock(return_value={"device_id": "phone", "session_id": "session"}),
                next_vision_frame=Mock(side_effect=[{"signature": (1,), "content": b"dummyJPEG"}, RuntimeError("ended")]),
            )
            with patch.dict(os.environ, {"EATVENTURE_WEB_TOKEN": ""}):
                app = create_app(root=root, token=token, supervisor=supervisor)
            with TestClient(app) as client:
                url = "/api/devices/phone/vision/stream"
                self.assertEqual(client.get(url).status_code, 401)
                supervisor.open_vision_stream.assert_not_called()
                response = client.get(url, headers={"Authorization": f"Bearer {token}"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers["content-type"], "multipart/x-mixed-replace; boundary=frame")
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(response.headers["x-accel-buffering"], "no")
                self.assertIn(b"Content-Length: 9\r\n\r\ndummyJPEG\r\n", response.content)
                supervisor.open_vision_stream.side_effect = RuntimeError("stopped")
                self.assertEqual(client.get(url, headers={"Authorization": f"Bearer {token}"}).status_code, 409)
                supervisor.open_vision_stream.side_effect = KeyError("unknown")
                self.assertEqual(client.get(url, headers={"Authorization": f"Bearer {token}"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()

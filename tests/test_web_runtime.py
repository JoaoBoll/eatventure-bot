"""Telemetria local do painel com relógio simulado e arquivos temporários."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.runtime import RuntimeControl


class WebRuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.status = self.root / "run" / "status.json"
        self.stop = self.root / "run" / "stop"
        self.runtime = RuntimeControl("phone-a", self.status, self.stop, session_id="session-123")

    def read_status(self):
        return json.loads(self.status.read_text(encoding="utf-8"))

    def test_status_identifies_process_device_and_session(self):
        summary = {"estado": "UPGRADE", "voos": 2, "reformas": 3, "acoes": 40}
        with patch("core.runtime.os.getpid", return_value=1234), patch(
            "core.runtime.time.time", return_value=1000.5,
        ):
            self.runtime.publish(summary, fps=12.5, capture_fps=30, templates=156, battery=(80, True, 2), force=True)

        data = self.read_status()
        self.assertEqual((data["pid"], data["device_id"], data["session_id"]), (1234, "phone-a", "session-123"))
        self.assertEqual(data["updated_at"], 1000.5)
        self.assertEqual(data["state"], "UPGRADE")
        self.assertEqual(data["summary"], summary)
        self.assertEqual((data["fps"], data["capture_fps"], data["templates"]), (12.5, 30, 156))
        self.assertEqual(data["battery"], {"level": 80, "charging": True, "age": 2})
        self.assertEqual((data["voos"], data["reformas"], data["acoes"]), (2, 3, 40))

    def test_status_replaces_complete_file_atomically(self):
        self.runtime.publish({"estado": "ANTES"}, force=True)
        original_replace = os.replace
        replaced = []

        def check_before_replace(source, destination):
            self.assertEqual(Path(source).parent, self.status.parent)
            self.assertEqual(Path(destination), self.status)
            self.assertEqual(self.read_status()["state"], "ANTES")
            self.assertEqual(json.loads(Path(source).read_text(encoding="utf-8"))["state"], "DEPOIS")
            replaced.append(Path(source))
            original_replace(source, destination)

        with patch("core.runtime.os.replace", side_effect=check_before_replace):
            self.runtime.publish({"estado": "DEPOIS"}, force=True)

        self.assertEqual(self.read_status()["state"], "DEPOIS")
        self.assertEqual(len(replaced), 1)
        self.assertFalse(replaced[0].exists())
        self.assertEqual(list(self.status.parent.iterdir()), [self.status])

    def test_failed_replacement_preserves_old_status_and_cleans_temporary_file(self):
        self.runtime.publish({"estado": "ANTES"}, force=True)
        with patch("core.runtime.os.replace", side_effect=PermissionError("ocupado")):
            self.runtime.publish({"estado": "DEPOIS"}, force=True)

        self.assertEqual(self.read_status()["state"], "ANTES")
        self.assertEqual(list(self.status.parent.iterdir()), [self.status])

    def test_invalid_telemetry_preserves_old_status(self):
        self.runtime.publish({"estado": "ANTES"}, force=True)
        for summary in ({"fps": float("nan")}, {"acoes": {"invalid"}}):
            with self.subTest(summary=summary):
                self.runtime.publish(summary, force=True)
                self.assertEqual(self.read_status()["state"], "ANTES")
                self.assertEqual(list(self.status.parent.iterdir()), [self.status])

    def test_stop_file_and_stop_event_request_cooperative_shutdown(self):
        self.assertFalse(self.runtime.should_stop())
        self.runtime.stopped.set()
        self.assertTrue(self.runtime.should_stop())
        self.runtime.stopped.clear()
        self.assertFalse(self.runtime.should_stop())
        self.stop.parent.mkdir(parents=True)
        self.stop.write_text("stale-session\n", encoding="utf-8")
        self.assertFalse(self.runtime.should_stop())
        self.stop.write_text("session-123\n", encoding="utf-8")
        self.assertTrue(self.runtime.should_stop())

    def test_status_frequency_is_limited_and_force_bypasses_it(self):
        with patch("core.runtime.time.monotonic", return_value=10.0) as clock:
            self.runtime.publish({"estado": "PRIMEIRO"})
            clock.return_value = 10.1
            self.runtime.publish({"estado": "CEDO"})
            self.assertEqual(self.read_status()["state"], "PRIMEIRO")
            clock.return_value = 10.51
            self.runtime.publish({"estado": "SEGUNDO"})
            self.assertEqual(self.read_status()["state"], "SEGUNDO")
            clock.return_value = 10.52
            self.runtime.publish({"estado": "FINAL"}, force=True)
            self.assertEqual(self.read_status()["state"], "FINAL")


if __name__ == "__main__":
    unittest.main()

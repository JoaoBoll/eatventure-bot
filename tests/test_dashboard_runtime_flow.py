"""Fluxo de telemetria e prévia em memória, sem ADB ou processos reais."""

import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

try:
    import httpx
    from fastapi.testclient import TestClient
except ImportError as error:
    raise unittest.SkipTest("Testes do painel precisam de fastapi e httpx.") from error

from core.device_profiles import ProfileStore
from core.runtime import RuntimeControl
from dashboard.server import create_app
from dashboard.supervisor import BotSupervisor, _Run


class DashboardRuntimeFlowTests(unittest.TestCase):
    def test_metrics_and_preview_update_when_launcher_and_worker_have_different_pids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "local"
            store = ProfileStore(state_root / "devices")
            store.save("hardware-1", {"name": "Cozinha"})
            devices = SimpleNamespace(
                last_error=None,
                list=Mock(side_effect=AssertionError("O fluxo de runtime não pode descobrir dispositivos.")),
            )
            supervisor = BotSupervisor(root, state_root=state_root, store=store, devices=devices)
            process = Mock(pid=111)
            process.poll.return_value = None
            status = state_root / "runtime" / "hardware.status.json"
            stop = state_root / "runtime" / "hardware.stop"
            run = _Run(process, 990.0, "session-123", "usb-1", status, stop)
            supervisor._runs["hardware-1"] = run
            runtime = RuntimeControl("hardware-1", status, stop, "session-123")

            with (
                patch.dict(os.environ, {}, clear=True),
                patch("core.runtime.os.getpid", return_value=222),
                patch("core.runtime.time.time", return_value=1000.0) as clock,
                patch("core.preview._encode_jpeg", return_value=b"simulatedJPEG") as encode,
                patch("dashboard.supervisor.subprocess.run", side_effect=AssertionError("Nenhum comando pode ser executado.")) as execute,
                patch("dashboard.supervisor.subprocess.Popen", side_effect=AssertionError("Nenhum processo pode ser iniciado.")) as spawn,
                patch.object(supervisor, "shutdown") as shutdown,
                patch.object(supervisor, "_cleanup_group", side_effect=AssertionError("Nenhum grupo real pode ser encerrado.")),
                patch.object(supervisor, "_signal", side_effect=AssertionError("Nenhum sinal real pode ser enviado.")),
            ):
                runtime.publish(
                    {"estado": "NORMAL", "voos": 1, "reformas": 2, "acoes": 3},
                    fps=12.5, capture_fps=30.0, templates=156, battery=(80, False, 1.0), force=True,
                )
                app = create_app(root=root, state_root=state_root, token=None, supervisor=supervisor)
                with TestClient(app) as client:
                    session = client.get("/api/session")
                    self.assertEqual(session.status_code, 200)
                    self.assertEqual(session.json(), {"authenticated": True, "auth_required": False})

                    first = client.get("/api/runtime")
                    self.assertEqual(first.status_code, 200, first.text)
                    device = first.json()["devices"][0]
                    self.assertEqual(device["pid"], 111)
                    self.assertEqual(device["metrics"]["pid"], 222)
                    self.assertEqual(run.worker_pid, 222)
                    self.assertEqual(device["name"], "Cozinha")
                    self.assertEqual(
                        [device["metrics"][key] for key in ("fps", "capture_fps", "voos", "reformas", "acoes")],
                        [12.5, 30.0, 1, 2, 3],
                    )

                    enabled = client.post("/api/devices/hardware-1/vision", json={"enabled": True})
                    self.assertEqual(enabled.status_code, 200, enabled.text)
                    self.assertEqual(enabled.json(), {"enabled": True})
                    frame = object()
                    self.assertTrue(runtime.preview.publish(frame))
                    encode.assert_called_once_with(frame)
                    preview = client.get("/api/devices/hardware-1/vision")
                    self.assertEqual(preview.status_code, 200, preview.text)
                    self.assertEqual(preview.headers["content-type"], "image/jpeg")
                    self.assertEqual(preview.content, b"simulatedJPEG")

                    clock.return_value = 1001.0
                    runtime.publish(
                        {"estado": "RENOVATE", "voos": 2, "reformas": 3, "acoes": 10},
                        fps=18.0, capture_fps=40.0, templates=157, battery=(79, False, 1.0), force=True,
                    )
                    second = client.get("/api/runtime")
                    self.assertEqual(second.status_code, 200, second.text)
                    metrics = second.json()["devices"][0]["metrics"]
                    self.assertEqual(metrics["updated_at"], 1001.0)
                    self.assertEqual(metrics["state"], "RENOVATE")
                    self.assertEqual(
                        [metrics[key] for key in ("fps", "capture_fps", "voos", "reformas", "acoes", "templates")],
                        [18.0, 40.0, 2, 3, 10, 157],
                    )
                    devices.list.assert_not_called()
                shutdown.assert_called_once_with()
                execute.assert_not_called()
                spawn.assert_not_called()
                process.send_signal.assert_not_called()
                process.wait.assert_not_called()
            self.assertFalse((state_root / "dashboard.token").exists())


if __name__ == "__main__":
    unittest.main()

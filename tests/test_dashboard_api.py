"""API do painel em memória, sem rede, ADB ou processos do bot."""

from pathlib import Path
import os
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
from dashboard.server import MAX_BODY, create_app
from dashboard.supervisor import BotSupervisor


class FakeSupervisor:
    def __init__(self, store):
        self.store = store
        self.devices = SimpleNamespace(last_error=None)
        self.calls = []
        self.failures = {}
        self.frames = {}
        self.runtime_devices = []
        self.runtime_calls = 0

    def list_devices(self, refresh=False):
        return [{"id": "phone-a"}, {"id": "phone-b"}]

    def require_device(self, device_id):
        if device_id not in {"phone-a", "phone-b"}:
            raise KeyError(device_id)

    def logs(self, device_id, after=0):
        self.calls.append(("logs", device_id, after))
        return {"lines": [{"id": after + 1, "text": "linha simulada"}], "cursor": after + 1}

    def runtime_state(self):
        self.runtime_calls += 1
        return {"devices": self.runtime_devices, "error": self.devices.last_error}

    def start(self, device_id):
        self.calls.append(("start", device_id))
        if device_id in self.failures:
            raise self.failures[device_id]
        if device_id not in {"phone-a", "phone-b"}:
            raise KeyError(device_id)
        return {"id": device_id, "process_state": "running"}

    def stop(self, device_id):
        self.calls.append(("stop", device_id))
        return {"id": device_id, "process_state": "stopped"}

    def request_vision(self, device_id, enabled):
        self.calls.append(("vision", device_id, enabled))
        return {"enabled": enabled}

    def vision_frame(self, device_id):
        self.calls.append(("vision_frame", device_id))
        if device_id not in self.frames:
            raise RuntimeError("Aguardando frame da visão IA.")
        return self.frames[device_id]

    def shutdown(self):
        self.calls.append(("shutdown", None))


class DashboardApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = ProfileStore(self.root / "local" / "devices")
        self.supervisor = FakeSupervisor(self.store)
        self.token = "local-dashboard-test-token-123456789"
        self.auth = {"Authorization": f"Bearer {self.token}"}
        self.client = self.make_client(token=self.token)

    def make_client(self, **options):
        client = TestClient(create_app(
            root=self.root,
            state_root=self.root / "local",
            supervisor=self.supervisor,
            **options,
        ))
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return client

    def test_api_requires_valid_token_before_any_device_action(self):
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.client.get("/api/state", headers=headers).status_code, 401)
                self.assertEqual(self.client.post("/api/devices/phone-a/stop", headers=headers).status_code, 401)
                session = self.client.get("/api/session", headers=headers)
                self.assertEqual(session.status_code, 200)
                self.assertEqual(session.json(), {"authenticated": False, "auth_required": True})
        self.assertEqual(self.supervisor.calls, [])
        response = self.client.get("/api/session", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"authenticated": True, "auth_required": True})
        state = self.client.get("/api/state", headers=self.auth)
        self.assertEqual(state.status_code, 200, state.text)
        self.assertEqual([device["id"] for device in state.json()["devices"]], ["phone-a", "phone-b"])

    def test_default_mode_opens_api_without_creating_token_file(self):
        with patch.dict(os.environ, {"EATVENTURE_WEB_TOKEN": ""}), patch(
            "dashboard.server.access_token",
        ) as token_loader:
            client = self.make_client()
        self.assertEqual(client.get("/api/session").json(), {"authenticated": True, "auth_required": False})
        self.assertEqual(client.get("/api/state").status_code, 200)
        self.assertEqual(client.get("/api/runtime").status_code, 200)
        token_loader.assert_not_called()
        self.assertFalse((self.root / "local" / "dashboard.token").exists())

    def test_server_shutdown_requests_graceful_uvicorn_exit(self):
        app = create_app(
            root=self.root, state_root=self.root / "local",
            supervisor=self.supervisor, token=self.token,
        )
        server = SimpleNamespace(should_exit=False)
        app.state.web_server = server
        client = TestClient(app)
        response = client.post("/api/server/shutdown", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(server.should_exit)

    def test_server_shutdown_endpoint_requires_authentication(self):
        response = self.client.post("/api/server/shutdown")
        self.assertEqual(response.status_code, 401)

    def test_runtime_endpoint_requires_auth_before_reading_metrics(self):
        response = self.client.get("/api/runtime")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.supervisor.runtime_calls, 0)
        response = self.client.get("/api/runtime", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.supervisor.runtime_calls, 1)

    def test_runtime_endpoint_returns_live_metrics_without_device_discovery(self):
        self.supervisor.list_devices = Mock(side_effect=AssertionError("O polling não deve descobrir dispositivos."))
        self.supervisor.runtime_devices = [{
            "id": "phone-a", "running": True, "status": "running", "pid": 23456,
            "metrics": {"fps": 12.5, "capture_fps": 30.0, "voos": 2, "reformas": 3, "acoes": 40},
        }]
        first = self.client.get("/api/runtime", headers=self.auth)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["devices"][0]["metrics"]["voos"], 2)
        self.supervisor.runtime_devices[0]["metrics"].update(fps=18.0, voos=4)
        second = self.client.get("/api/runtime", headers=self.auth)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["devices"][0]["metrics"]["voos"], 4)
        self.assertEqual(second.json()["devices"][0]["metrics"]["fps"], 18.0)
        self.assertEqual(self.supervisor.runtime_calls, 2)
        self.supervisor.list_devices.assert_not_called()

    def test_require_token_explicitly_loads_token_and_protects_api(self):
        with patch.dict(os.environ, {"EATVENTURE_WEB_TOKEN": ""}), patch(
            "dashboard.server.access_token", return_value=self.token,
        ) as token_loader:
            client = self.make_client(require_token=True)
        token_loader.assert_called_once()
        self.assertEqual(client.get("/api/session").json(), {"authenticated": False, "auth_required": True})
        self.assertEqual(client.get("/api/state").status_code, 401)
        self.assertEqual(client.get("/api/state", headers=self.auth).status_code, 200)

    def test_environment_token_activates_auth_without_requirement_flag(self):
        with patch.dict(os.environ, {"EATVENTURE_WEB_TOKEN": self.token}):
            client = self.make_client()
        self.assertEqual(client.get("/api/session").json(), {"authenticated": False, "auth_required": True})
        self.assertEqual(client.get("/api/state").status_code, 401)
        self.assertEqual(client.get("/api/state", headers=self.auth).status_code, 200)
        self.assertFalse((self.root / "local" / "dashboard.token").exists())

    def test_foreign_origin_is_rejected_and_local_origin_is_accepted(self):
        for origin in ("http://other-host", "https://testserver", "http://["):
            with self.subTest(origin=origin):
                rejected = self.client.get("/api/session", headers={**self.auth, "Origin": origin})
                self.assertEqual(rejected.status_code, 403)
        accepted = self.client.get("/api/session", headers={**self.auth, "Origin": "http://testserver"})
        self.assertEqual(accepted.status_code, 200)

    def test_profile_validation_prevents_arbitrary_settings(self):
        for payload in (
            {"settings": {"shape_threshold": 1.5}},
            {"settings": {"capture_max_fps": True}},
            {"settings": {"adb_path": "/bin/sh"}},
            {"device_id": "phone-b", "settings": {"shape_threshold": 0.9}},
            {"item_thresholds": {"food/../../outside.png": 0.9}},
        ):
            with self.subTest(payload=payload):
                response = self.client.put("/api/devices/phone-a/profile", json=payload, headers=self.auth)
                self.assertEqual(response.status_code, 400, response.text)
        self.assertFalse(self.store.path_for("phone-a").exists())
        self.assertEqual(self.supervisor.calls, [])

    def test_save_affects_only_selected_device_without_starting_it(self):
        payload = {
            "name": "Celular A",
            "settings": {"shape_threshold": 0.91},
            "category_thresholds": {"food": 0.92},
            "item_thresholds": {"food/1.png": 0.93},
            "run": {"save_log": True},
        }
        response = self.client.put("/api/devices/phone-a/profile", json=payload, headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["settings"], {"shape_threshold": 0.91})
        first = self.client.get("/api/devices/phone-a/profile", headers=self.auth).json()
        second = self.client.get("/api/devices/phone-b/profile", headers=self.auth).json()
        self.assertEqual(first["item_thresholds"], {"food/1.png": 0.93})
        self.assertEqual(second["settings"], {})
        self.assertEqual(second["category_thresholds"], {})
        self.assertEqual(second["item_thresholds"], {})
        self.assertFalse(second["run"]["save_log"])
        self.assertFalse(self.store.path_for("phone-b").exists())
        self.assertEqual(self.supervisor.calls, [])

    def test_unknown_device_cannot_receive_a_profile(self):
        response = self.client.put("/api/devices/unknown/profile", json={}, headers=self.auth)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.store.path_for("unknown").exists())

    def test_opening_profile_and_logs_does_not_list_devices(self):
        self.supervisor.list_devices = Mock(side_effect=AssertionError("Abrir detalhes não deve aguardar a descoberta."))
        profile = self.client.get("/api/devices/phone-a/profile", headers=self.auth)
        logs = self.client.get("/api/devices/phone-a/logs?after=3", headers=self.auth)

        self.assertEqual(profile.status_code, 200, profile.text)
        self.assertEqual(profile.json()["device_id"], "phone-a")
        self.assertEqual(logs.status_code, 200, logs.text)
        self.assertEqual(logs.json(), {"lines": [{"id": 4, "text": "linha simulada"}], "cursor": 4})
        self.supervisor.list_devices.assert_not_called()

    def test_real_supervisor_cached_profile_is_accessible_without_discovery(self):
        devices = Mock(last_error=None)
        devices.list.side_effect = AssertionError("O dispositivo já está no catálogo local.")
        supervisor = BotSupervisor(self.root, store=self.store, devices=devices)
        supervisor._device_cache["phone-a"] = {"id": "phone-a"}
        self.store.save("phone-a", {"name": "Celular salvo"})
        with TestClient(create_app(
            root=self.root, state_root=self.root / "local", token=self.token, supervisor=supervisor,
        )) as client:
            response = client.get("/api/devices/phone-a/profile", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["name"], "Celular salvo")
        devices.list.assert_not_called()

    def test_bulk_action_preserves_partial_results_and_deduplicates_devices(self):
        self.supervisor.failures["phone-b"] = RuntimeError("Dispositivo desconectado.")
        response = self.client.post("/api/actions", json={
            "action": "start", "device_ids": ["phone-a", "phone-b", "phone-a"],
        }, headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        results = response.json()["results"]
        self.assertEqual([result["device_id"] for result in results], ["phone-a", "phone-b"])
        self.assertTrue(results[0]["ok"])
        self.assertFalse(results[1]["ok"])
        self.assertIn("desconectado", results[1]["error"])
        self.assertEqual(sorted(self.supervisor.calls), [("start", "phone-a"), ("start", "phone-b")])

    def test_invalid_json_and_oversized_request_are_rejected(self):
        invalid = self.client.put("/api/devices/phone-a/profile", content="{", headers=self.auth)
        non_object = self.client.put("/api/devices/phone-a/profile", json=[], headers=self.auth)
        large = self.client.put("/api/devices/phone-a/profile", content=b"x" * (MAX_BODY + 1), headers=self.auth)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(non_object.status_code, 400)
        self.assertEqual(large.status_code, 413)
        self.assertFalse(self.store.path_for("phone-a").exists())

    def test_chunked_body_cannot_bypass_size_limit(self):
        chunks = iter((b"x" * MAX_BODY, b"x"))
        response = self.client.put("/api/devices/phone-a/profile", content=chunks, headers=self.auth)
        self.assertEqual(response.status_code, 413)
        self.assertFalse(self.store.path_for("phone-a").exists())

    def test_vision_enable_and_disable_only_request_selected_device(self):
        for enabled in (True, False):
            response = self.client.post("/api/devices/phone-a/vision", json={"enabled": enabled}, headers=self.auth)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"enabled": enabled})
        self.assertEqual(self.supervisor.calls, [("vision", "phone-a", True), ("vision", "phone-a", False)])

    def test_vision_rejects_non_boolean_enabled_without_requesting_device(self):
        for payload in ({}, {"enabled": "false"}, {"enabled": 1}, {"enabled": None}):
            with self.subTest(payload=payload):
                response = self.client.post("/api/devices/phone-a/vision", json=payload, headers=self.auth)
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.supervisor.calls, [])

    def test_vision_frame_is_served_as_jpeg_without_reading_real_images(self):
        frame = self.root / "simulated-preview.jpg"
        frame.write_bytes(b"simulated-jpeg-frame")
        self.supervisor.frames["phone-a"] = frame
        response = self.client.get("/api/devices/phone-a/vision", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertEqual(response.content, b"simulated-jpeg-frame")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.supervisor.calls, [("vision_frame", "phone-a")])

    def test_vision_without_frame_returns_conflict(self):
        response = self.client.get("/api/devices/phone-a/vision", headers=self.auth)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("frame", response.json()["detail"])

    def test_vision_routes_require_auth_before_requesting_device(self):
        self.assertEqual(self.client.get("/api/devices/phone-a/vision").status_code, 401)
        self.assertEqual(self.client.post("/api/devices/phone-a/vision", json={"enabled": True}).status_code, 401)
        self.assertEqual(self.supervisor.calls, [])

    def test_login_and_static_files_are_accessible_without_authentication(self):
        for path, content_type in (
            ("/", "text/html"),
            ("/static/styles.css", "text/css"),
            ("/static/app.js", "javascript"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn(content_type, response.headers["content-type"])
                self.assertEqual(response.headers["x-content-type-options"], "nosniff")
                self.assertIn("script-src 'self'", response.headers["content-security-policy"])


if __name__ == "__main__":
    unittest.main()

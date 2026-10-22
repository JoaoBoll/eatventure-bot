"""API do painel em memória, sem rede, ADB ou processos do bot."""

from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

try:
    import httpx
    from fastapi.testclient import TestClient
except ImportError as error:
    raise unittest.SkipTest("Testes do painel precisam de fastapi e httpx.") from error

from core.device_profiles import ProfileStore
from dashboard.server import MAX_BODY, create_app


class FakeSupervisor:
    def __init__(self, store):
        self.store = store
        self.devices = SimpleNamespace(last_error=None)
        self.calls = []
        self.failures = {}

    def list_devices(self, refresh=False):
        return [{"id": "phone-a"}, {"id": "phone-b"}]

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
        self.client = TestClient(create_app(
            root=self.root,
            state_root=self.root / "local",
            token=self.token,
            supervisor=self.supervisor,
        ))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_api_requires_valid_token_before_any_device_action(self):
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.client.get("/api/state", headers=headers).status_code, 401)
                self.assertEqual(self.client.post("/api/devices/phone-a/stop", headers=headers).status_code, 401)
        self.assertEqual(self.supervisor.calls, [])
        response = self.client.get("/api/session", headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"authenticated": True})
        state = self.client.get("/api/state", headers=self.auth)
        self.assertEqual(state.status_code, 200, state.text)
        self.assertEqual([device["id"] for device in state.json()["devices"]], ["phone-a", "phone-b"])

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

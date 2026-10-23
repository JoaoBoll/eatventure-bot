"""CLI do painel com aplicação e servidor completamente simulados."""

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import web


class WebCliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name) / "local"
        self.app = SimpleNamespace(state=SimpleNamespace(auth_required=False))
        self.create_app = Mock(return_value=self.app)
        self.run_server = Mock()
        modules = patch.dict(sys.modules, {
            "uvicorn": SimpleNamespace(run=self.run_server),
            "dashboard.server": SimpleNamespace(create_app=self.create_app),
        })
        modules.start()
        self.addCleanup(modules.stop)
        environment = patch.dict(os.environ, {"EATVENTURE_WEB_TOKEN": ""})
        environment.start()
        self.addCleanup(environment.stop)
        self.output = io.StringIO()
        output = redirect_stdout(self.output)
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def test_default_cli_disables_token_and_passes_server_options(self):
        web.main(["--state-dir", str(self.state)])

        arguments = self.create_app.call_args.kwargs
        self.assertEqual(arguments["state_root"], self.state)
        self.assertFalse(arguments.get("require_token", False))
        self.run_server.assert_called_once_with(
            self.app, host="0.0.0.0", port=8765, workers=1, access_log=False,
        )
        self.assertIn("Painel: http://localhost:8765", self.output.getvalue())
        self.assertIn("Na rede: http://IP_DO_SERVIDOR:8765", self.output.getvalue())
        self.assertNotIn("dashboard.token", self.output.getvalue())
        self.assertFalse(self.state.exists())

    def test_require_token_is_explicit_and_preserves_custom_host_port(self):
        self.app.state.auth_required = True
        web.main([
            "--state-dir", str(self.state), "--host", "127.0.0.1", "--port", "9000", "--require-token",
        ])

        self.assertTrue(self.create_app.call_args.kwargs["require_token"])
        self.run_server.assert_called_once_with(
            self.app, host="127.0.0.1", port=9000, workers=1, access_log=False,
        )
        self.assertIn("Painel: http://127.0.0.1:9000", self.output.getvalue())
        self.assertNotIn("Na rede:", self.output.getvalue())
        self.assertFalse(self.state.exists())

    def test_environment_token_does_not_need_requirement_flag_or_appear_in_output(self):
        token = "environment-token-for-test-only-123456789"
        self.app.state.auth_required = True
        with patch.dict(os.environ, {"EATVENTURE_WEB_TOKEN": token}):
            web.main(["--state-dir", str(self.state)])

        self.assertFalse(self.create_app.call_args.kwargs.get("require_token", False))
        self.run_server.assert_called_once()
        self.assertNotIn(token, self.output.getvalue())

    def test_invalid_port_stops_before_creating_application(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            web.main(["--state-dir", str(self.state), "--port", "65536"])

        self.assertEqual(raised.exception.code, 2)
        self.create_app.assert_not_called()
        self.run_server.assert_not_called()
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()

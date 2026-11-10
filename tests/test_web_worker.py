"""Entrada web validada sem importar ou executar o bot e suas dependências."""

import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import core
import web_worker


class WebWorkerTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Mock()
        self.runtime.should_stop.return_value = False
        self.bot_main = Mock(return_value=0)
        environment = {
            "EATVENTURE_PROFILE": "mock-profile.json",
            "EATVENTURE_DEVICE_ID": "hardware-1",
            "EATVENTURE_STATUS_FILE": "mock.status.json",
            "EATVENTURE_STOP_FILE": "mock.stop",
            "EATVENTURE_SESSION_ID": "session-123",
        }
        patches = [
            patch.object(core, "config", SimpleNamespace(), create=True),
            patch("core.runtime.RuntimeControl", return_value=self.runtime),
            patch.dict(sys.modules, {"main": SimpleNamespace(main=self.bot_main)}),
            patch.dict(os.environ, environment),
        ]
        for item in patches:
            result = item.start()
            self.addCleanup(item.stop)
            if hasattr(result, "return_value") and result.return_value is self.runtime:
                self.runtime_class = result

    def test_worker_passes_its_runtime_to_bot_and_cleans_preview(self):
        self.assertEqual(web_worker.main(), 0)
        self.runtime_class.assert_called_once_with("hardware-1", "mock.status.json", "mock.stop", "session-123")
        self.runtime.install_signals.assert_called_once_with()
        self.bot_main.assert_called_once_with(runtime=self.runtime)
        self.runtime.preview.stop.assert_called_once_with()
        self.assertEqual(self.runtime.publish.call_args_list, [
            call({"estado": "iniciando"}, force=True),
            call({"estado": "encerrado"}, force=True),
        ])

    def test_worker_cleans_preview_and_publishes_status_after_bot_failure(self):
        self.bot_main.side_effect = RuntimeError("simulated failure")
        with self.assertRaisesRegex(RuntimeError, "simulated failure"):
            web_worker.main()
        self.runtime.preview.stop.assert_called_once_with()
        self.assertEqual(self.runtime.publish.call_args, call({"estado": "encerrado"}, force=True))

    def test_cooperative_stop_before_start_does_not_run_bot(self):
        self.runtime.should_stop.return_value = True
        self.assertIsNone(web_worker.main())
        self.bot_main.assert_not_called()
        self.runtime.preview.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

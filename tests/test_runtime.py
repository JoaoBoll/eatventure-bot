"""Integração dos loops com captura e serviços simulados."""

import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main
import main_layouts
from core.device_session import device_session


class RuntimeTests(unittest.TestCase):
    def capture(self, frames):
        capture = Mock()
        capture.get_frame.side_effect = frames + [(None, 0, 0.0)]
        capture.is_running.side_effect = [True] * len(frames) + [False]
        return capture

    def vision(self, frame):
        vision = Mock()
        vision.get_input.return_value = (frame, [{"category": "food"}], 1.0)
        return vision

    def test_loop_nao_reprocessa_frame_repetido(self):
        frame = SimpleNamespace(shape=(1600, 720, 3))
        capture = self.capture([(frame, 1, 1.0)] * 2)
        vision, state = self.vision(frame), Mock()
        with patch.object(main, "SHOW_AI_VISION", False):
            main.run_loop(capture, vision, Mock(), state, Mock(), Mock())
        vision.set_frame.assert_called_once()
        state.update.assert_called_once()

    def test_mudanca_de_resolucao_descarta_deteccoes_antigas(self):
        frame = SimpleNamespace(shape=(1600, 720, 3))
        old = SimpleNamespace(shape=(2400, 1080, 3))
        state, actions = Mock(), Mock()
        with patch.object(main, "SHOW_AI_VISION", False):
            main.run_loop(
                self.capture([(frame, 1, 1.0)]), self.vision(old),
                Mock(), state, actions, Mock(),
            )
        state.update.assert_not_called()
        actions.set_frame_size.assert_called_once_with(720, 1600)

    def test_sem_observacao_nao_explora_mesmo_sem_limite_de_idade(self):
        from core import state_machine

        frame = SimpleNamespace(shape=(1600, 720, 3))
        actions = Mock()
        actions.is_busy.return_value = False
        actions.last_finished_at = 0.0
        state = state_machine.StateMachine(actions)
        state.exploration_delay = 0.0
        vision = Mock()
        vision.get_input.return_value = (None, [], 0.0)
        with patch.object(main, "SHOW_AI_VISION", False), patch.object(
            state_machine, "MAX_DETECTION_AGE", None
        ):
            main.run_loop(
                self.capture([(frame, 1, 1.0)]), vision,
                Mock(), state, actions, Mock(),
            )
        actions.swipe.assert_not_called()
        actions.execute.assert_not_called()

    def test_esc_funciona_sem_frame_novo(self):
        capture = Mock()
        capture.get_frame.return_value = (None, 0, 0.0)
        with patch.object(main, "SHOW_AI_VISION", True), patch.object(
            main.cv2, "waitKey", return_value=27
        ):
            main.run_loop(capture, Mock(), Mock(), Mock(), Mock(), Mock())
        capture.get_frame.assert_called_once()

    def test_atualizador_nao_redetecta_frame_repetido(self):
        frame = SimpleNamespace(shape=(1600, 720, 3))
        detector = Mock()
        detector.template_coverage.return_value = (0, 0)
        with patch.object(main_layouts, "SHOW_AI_VISION", False):
            main_layouts.run_loop(self.capture([(frame, 1, 1.0)] * 2), detector)
        detector.detect.assert_called_once_with(frame)

    def test_config_dataset_save_e_respeitada_sem_flag(self):
        with ExitStack() as stack:
            for name in ("SHOW_AI_VISION", "SHOW_SCRCPY", "STATUS_PANEL"):
                stack.enter_context(patch.object(main, name, False))
            stack.enter_context(patch.dict(main.os.environ, {"EATVENTURE_SESSION_ID": ""}))
            stack.enter_context(patch.object(main, "DATASET_SAVE", True))
            stack.enter_context(patch.object(main.devices, "resolver", return_value="device"))
            stack.enter_context(patch.object(main.devices, "hardware_serial", return_value="hardware"))
            stack.enter_context(patch.object(main, "device_session", return_value=nullcontext(lambda: False)))
            for name in ("ScreenCapture", "Detector", "VisionWorker", "ActionManager", "StateMachine", "BatteryMonitor", "run_loop"):
                stack.enter_context(patch.object(main, name))
            stack.enter_context(patch.object(main.cv2, "destroyAllWindows"))
            recorder = stack.enter_context(patch.object(main, "DatasetRecorder"))
            main.main([])
            recorder.assert_called_once_with(session=None)
            recorder.return_value.start.assert_called_once()
            recorder.return_value.stop.assert_called_once()

    def test_falha_de_inicializacao_nao_abre_espelho(self):
        with patch.object(main.devices, "resolver", return_value="device"), patch.object(
            main.devices, "hardware_serial", return_value="hardware"
        ), patch.object(
            main, "device_session", return_value=nullcontext(lambda: False)
        ), patch.object(
            main, "SHOW_SCRCPY", True
        ), patch.object(main, "Detector", side_effect=RuntimeError("templates")), patch.object(
            main, "start_scrcpy"
        ) as start_scrcpy:
            with self.assertRaisesRegex(RuntimeError, "templates"):
                main.main([])
        start_scrcpy.assert_not_called()

    def test_nova_sessao_aguarda_a_anterior_para_o_mesmo_aparelho(self):
        requested = Event()
        with TemporaryDirectory() as directory, ThreadPoolExecutor() as pool, patch(
            "core.device_session.logger.info", side_effect=lambda *args: requested.set(),
        ):
            path = Path(directory)

            def replacement():
                with device_session(path, "hardware", timeout=2) as should_stop:
                    return should_stop()

            with device_session(path, "hardware") as should_stop:
                incoming = pool.submit(replacement)
                self.assertTrue(requested.wait(timeout=2))
                self.assertTrue(should_stop())
                self.assertFalse(incoming.done())
                with device_session(path, "outra-unidade") as other_should_stop:
                    self.assertFalse(other_should_stop())

            self.assertFalse(incoming.result(timeout=2))


if __name__ == "__main__":
    unittest.main()

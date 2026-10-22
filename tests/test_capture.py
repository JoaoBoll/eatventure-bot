"""Regressões de captura sem dispositivo, sockets reais ou vídeo."""

import socket
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from capture import screen
from capture.screen import ScreenCapture


class CaptureTests(unittest.TestCase):
    def test_porta_automatica_e_cleanup_da_propria_instancia(self):
        capture = ScreenCapture("device")
        with patch.object(ScreenCapture, "PORT", 0), patch.object(
            screen.subprocess, "run", return_value=SimpleNamespace(stdout="39001\n")
        ) as run:
            capture._setup_forward()
            self.assertEqual(capture.PORT, 39001)
            self.assertIn("tcp:0", run.call_args.args[0])
            self.assertIn("--no-rebind", run.call_args.args[0])
            capture._cleanup_forward()
            self.assertEqual(run.call_args.args[0][-1], "tcp:39001")
            capture._cleanup_forward()
            self.assertEqual(run.call_count, 2)

    def test_porta_configurada_continua_disponivel(self):
        capture = ScreenCapture()
        with patch.object(ScreenCapture, "PORT", 39002), patch.object(
            screen.subprocess, "run", return_value=SimpleNamespace(stdout="")
        ):
            capture._setup_forward()
        self.assertEqual(capture.PORT, 39002)

    def test_parar_antes_de_iniciar_nao_chama_adb(self):
        with patch.object(screen.subprocess, "run") as run:
            ScreenCapture().stop()
        run.assert_not_called()

    def test_reinicio_limpa_recursos_da_conexao_encerrada(self):
        capture = ScreenCapture()
        capture.socket = Mock()
        with patch.object(capture, "stop") as stop, patch.object(
            capture, "_push_server", side_effect=RuntimeError("inicialização simulada")
        ):
            with self.assertRaises(RuntimeError):
                capture.start()
        stop.assert_called_once()

    def test_conexao_sem_dados_fecha_socket_ao_expirar(self):
        candidate = Mock()
        candidate.recv.side_effect = socket.timeout
        with patch.object(screen.socket, "socket", return_value=candidate), patch.object(
            screen.time, "monotonic", side_effect=[0.0, 0.1, 0.2, 2.1]
        ):
            self.assertEqual(ScreenCapture()._connect(1.0), (None, None))
        candidate.close.assert_called_once()

    def test_eof_no_primeiro_bloco_encerra_thread(self):
        capture = ScreenCapture()
        capture.running = True
        capture.first_chunk = b"h264"
        capture.socket = Mock()
        with patch.object(capture, "_decode", return_value=False):
            capture._capture_loop()
        self.assertFalse(capture.running)
        capture.socket.recv.assert_not_called()

    def test_falha_de_conversao_marca_captura_como_parada(self):
        capture = ScreenCapture()
        capture.running = True
        capture.first_chunk = b"h264"
        with patch.object(capture, "_decode", side_effect=RuntimeError("decoder")):
            with self.assertRaisesRegex(RuntimeError, "decoder"):
                capture._capture_loop()
        self.assertFalse(capture.running)

    def test_processos_usam_arquivos_de_servidor_distintos(self):
        first, second = ScreenCapture(), ScreenCapture()
        with patch.object(screen.random, "randint", side_effect=[1, 2]), patch.object(
            screen.av.CodecContext, "create"
        ), patch.object(screen.threading, "Thread"), patch.object(
            ScreenCapture, "_push_server"
        ), patch.object(ScreenCapture, "_setup_forward"), patch.object(
            ScreenCapture, "_start_server"
        ), patch.object(
            ScreenCapture, "_connect", return_value=(Mock(), b"h264")
        ), patch.object(ScreenCapture, "_wait_first_frame", return_value=True):
            first.start()
            second.start()
        self.assertNotEqual(first.DEVICE_JAR, second.DEVICE_JAR)
        self.assertTrue(first.DEVICE_JAR.endswith(".jar"))
        self.assertNotEqual(first.socket_name, second.socket_name)


if __name__ == "__main__":
    unittest.main()

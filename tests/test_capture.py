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
        with patch.object(ScreenCapture, "SERVER_VERSION", "4.1"), patch.object(
            capture, "stop"
        ) as stop, patch.object(
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
        with patch.object(ScreenCapture, "SERVER_VERSION", "4.1"), patch.object(
            screen.random, "randint", side_effect=[1, 2]
        ), patch.object(
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

    def test_versao_explicita_nao_executa_scrcpy(self):
        with patch.object(ScreenCapture, "SERVER_VERSION", "4.1"), patch.object(
            screen.subprocess, "run"
        ) as run:
            self.assertEqual(ScreenCapture()._resolve_server_version(), (4, 1))
        run.assert_not_called()

    def test_versao_instalada_preserva_patch_e_sufixo_por_instancia(self):
        resultado = SimpleNamespace(
            stdout="scrcpy 4.1.2-dev <https://github.com/Genymobile/scrcpy>\n",
            stderr="",
            returncode=0,
        )
        with patch.object(ScreenCapture, "SERVER_VERSION", None), patch.object(
            screen, "SCRCPY_PATH", "/usr/bin/scrcpy"
        ), patch.object(screen.subprocess, "run", return_value=resultado) as run:
            capture = ScreenCapture()
            self.assertEqual(capture._resolve_server_version(), (4, 1))
            self.assertEqual(capture.SERVER_VERSION, "4.1.2-dev")
            self.assertIsNone(ScreenCapture.SERVER_VERSION)
        self.assertEqual(run.call_args.args[0], ["/usr/bin/scrcpy", "--version"])
        self.assertGreater(run.call_args.kwargs["timeout"], 0)

    def test_versao_anterior_a_1_23_e_rejeitada_antes_do_adb(self):
        with patch.object(ScreenCapture, "SERVER_VERSION", "1.21"), patch.object(
            screen.subprocess, "run"
        ) as run, patch.object(screen.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, r"1\.23"):
                ScreenCapture().start()
        run.assert_not_called()
        popen.assert_not_called()

    def test_versao_instalada_tambem_e_lida_do_stderr(self):
        resultado = SimpleNamespace(stdout="", stderr="scrcpy 1.25\n")
        with patch.object(ScreenCapture, "SERVER_VERSION", None), patch.object(
            screen.subprocess, "run", return_value=resultado
        ):
            self.assertEqual(ScreenCapture()._resolve_server_version(), (1, 25))

    def test_servidor_legado_usa_socket_sem_scid(self):
        with patch.object(ScreenCapture, "SERVER_VERSION", "1.25"), patch.object(
            screen.av.CodecContext, "create"
        ), patch.object(screen.threading, "Thread"), patch.object(
            ScreenCapture, "_push_server"
        ), patch.object(ScreenCapture, "_setup_forward"), patch.object(
            ScreenCapture, "_start_server"
        ), patch.object(
            ScreenCapture, "_connect", return_value=(Mock(), b"h264")
        ), patch.object(ScreenCapture, "_wait_first_frame", return_value=True):
            capture = ScreenCapture()
            capture.start()
        self.assertEqual(capture.socket_name, "scrcpy")

    def test_opcoes_do_servidor_correspondem_a_versao(self):
        casos = (
            ("1.23", "raw_video_stream=true", False),
            ("1.25", "raw_video_stream=true", False),
            ("2.0", "raw_video_stream=true", True),
            ("2.1", "raw_stream=true", True),
            ("4.1", "raw_stream=true", True),
        )
        for versao, stream_cru, moderno in casos:
            with self.subTest(versao=versao), patch.object(
                ScreenCapture, "SERVER_VERSION", versao
            ), patch.object(screen.subprocess, "Popen") as popen, patch.object(
                screen.subprocess, "run"
            ) as run:
                capture = ScreenCapture("device")
                capture.scid = 1
                capture._start_server()
                comando = popen.call_args.args[0]
                posicao_servidor = comando.index("com.genymobile.scrcpy.Server")
                self.assertEqual(comando[posicao_servidor + 1], versao)
                self.assertIn("tunnel_forward=true", comando)
                self.assertIn("control=false", comando)
                self.assertIn("cleanup=false", comando)
                self.assertEqual(
                    [
                        arg for arg in comando
                        if arg.startswith(("raw_stream=", "raw_video_stream="))
                    ],
                    [stream_cru],
                )
                for opcao in ("scid=00000001", "audio=false", "video_codec=h264"):
                    if moderno:
                        self.assertIn(opcao, comando)
                    else:
                        self.assertNotIn(opcao, comando)
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

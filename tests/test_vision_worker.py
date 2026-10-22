"""Regressões do worker sem templates, imagens ou captura real."""

import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vision.worker import VisionWorker  # noqa: E402


class DetectorFalso:

    def __init__(self):
        self.recebidos = []

    def detect(self, frame, categories):
        self.recebidos.append((frame, categories))
        return [{"category": "box"}]


class VisionWorkerTests(unittest.TestCase):

    def test_preserva_frame_e_prioridade_das_categorias(self):
        detector = DetectorFalso()
        worker = VisionWorker(detector, interval=0)
        anterior, atual = object(), object()
        worker.set_categories(["close", "box"])
        worker.set_frame(anterior, timestamp=1.0)
        worker.set_frame(atual, timestamp=2.0)

        worker._pass()

        self.assertEqual(detector.recebidos, [(atual, ("close", "box"))])
        self.assertEqual(worker.get_input(), (atual, [{"category": "box"}], 2.0))

    def test_descarta_frame_anterior_ao_piso_sem_apagar_leitura(self):
        detector = DetectorFalso()
        worker = VisionWorker(detector, interval=0)
        anterior = object()
        worker.set_frame(anterior, timestamp=1.0)
        worker._pass()
        worker.set_frame_floor(3.0)
        worker.set_frame(object(), timestamp=2.0)

        worker._pass()

        self.assertEqual(len(detector.recebidos), 1)
        self.assertIs(worker.get_input()[0], anterior)
        self.assertEqual(worker.skipped_stale, 1)
        self.assertTrue(worker.waiting_settle)

    def test_descarta_resultado_que_ficou_velho_durante_deteccao(self):
        detector = DetectorFalso()
        worker = VisionWorker(detector, interval=0)
        anterior = object()
        worker.set_frame(anterior, timestamp=1.0)
        worker._pass()

        def detectar(frame, categories):
            worker.set_frame_floor(3.0)
            return [{"category": "close"}]

        detector.detect = detectar
        worker.set_frame(object(), timestamp=2.0)
        worker._pass()

        self.assertEqual(worker.get_input(), (anterior, [{"category": "box"}], 1.0))
        self.assertEqual(worker.skipped_stale, 1)
        self.assertTrue(worker.waiting_settle)

    def test_parada_interrompe_intervalo_longo(self):
        detectou = threading.Event()
        detector = DetectorFalso()

        def detectar(frame, categories):
            detectou.set()
            return []

        detector.detect = detectar
        worker = VisionWorker(detector, interval=60.0)
        worker.set_frame(object())
        worker.start()

        try:
            self.assertTrue(detectou.wait(timeout=2.0))
            inicio = time.monotonic()
            worker.stop()
            self.assertLess(time.monotonic() - inicio, 1.0)
            self.assertIsNone(worker.thread)
        finally:
            worker.stop()

    def test_parada_acorda_worker_sem_frame(self):
        worker = VisionWorker(DetectorFalso(), interval=0)
        worker.start()
        worker.stop()
        self.assertIsNone(worker.thread)

    def test_thread_presa_impede_outra_instancia(self):
        class ThreadPresa:

            def join(self, timeout):
                pass

            def is_alive(self):
                return True

        worker = VisionWorker(DetectorFalso(), interval=0)
        thread = ThreadPresa()
        worker.thread = thread
        worker.running = True

        worker.stop()
        worker.start()

        self.assertIs(worker.thread, thread)
        self.assertFalse(worker.running)

    def test_sucesso_limpa_erro_anterior(self):
        worker = VisionWorker(DetectorFalso(), interval=0)
        worker.last_error = "RuntimeError: falha anterior"
        worker.set_frame(object(), timestamp=2.0)

        worker._pass()

        self.assertIsNone(worker.last_error)


if __name__ == "__main__":
    unittest.main()

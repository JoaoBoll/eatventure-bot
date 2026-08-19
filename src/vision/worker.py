"""
Roda o detector numa thread separada.

Mudanças em relação à versão anterior:

1. As detecções carregam o TIMESTAMP do frame que as
   gerou. Antes o loop principal entregava um frame e lia
   o resultado do frame anterior sem saber a defasagem —
   e clicava em coordenada velha achando que era atual.

2. O worker aceita um FILTRO DE CATEGORIAS. Dentro da tela
   de upgrade só interessam 2 categorias, não 43.
"""

import threading
import time

from core import log
from core.config import VISION_INTERVAL
from core.metrics import DurationMeter, RateMeter

logger = log.get("vision")


class VisionWorker:

    def __init__(self, detector, interval=VISION_INTERVAL):

        self.detector = detector

        self.interval = interval

        self.running = False
        self.thread = None

        self.latest_frame = None
        self.latest_frame_time = 0.0

        self.detections = []
        self.detections_time = 0.0

        self.categories = None

        self.frame_lock = threading.Lock()
        self.detection_lock = threading.Lock()

        # Custo real de uma passada, e quantas por segundo.
        #
        # É o número que diz se o bot está enxergando rápido
        # o bastante — não o FPS da captura.
        self.last_duration = 0.0

        self.duration = DurationMeter()

        self.rate = RateMeter()

    # =====================================================
    # START / STOP
    # =====================================================

    def start(self):

        if self.running:
            return

        self.running = True

        self.thread = threading.Thread(
            target=self._run,
            name="vision-worker",
            daemon=True,
        )

        self.thread.start()

    def stop(self):

        self.running = False

        if self.thread:

            self.thread.join(timeout=5)

            self.thread = None

    # =====================================================
    # FRAME
    # =====================================================

    def set_frame(self, frame, timestamp=None):

        with self.frame_lock:

            self.latest_frame = frame

            self.latest_frame_time = (
                time.monotonic()
                if timestamp is None
                else timestamp
            )

    # =====================================================
    # CATEGORIAS
    # =====================================================

    def set_categories(self, categories):
        """
        Restringe a busca. None = todas.
        """

        with self.detection_lock:

            self.categories = (
                None
                if categories is None
                else set(categories)
            )

    # =====================================================
    # DETECÇÕES
    # =====================================================

    def get_detections(self):
        """
        Devolve (detecções, idade em segundos).

        A idade é a do frame que gerou as detecções, não a
        do momento da consulta — é ela que diz se vale
        clicar.
        """

        with self.detection_lock:

            detections = list(self.detections)

            frame_time = self.detections_time

        if not frame_time:
            return detections, 0.0

        return (
            detections,
            max(0.0, time.monotonic() - frame_time),
        )

    # =====================================================
    # ESTATÍSTICAS
    # =====================================================

    def get_fps(self):
        """
        Passadas do detector por segundo.
        """

        return self.rate.rate()

    def get_duration(self):
        """
        Custo médio de uma passada, em segundos.
        """

        return self.duration.average()

    # =====================================================
    # WORKER
    # =====================================================

    def _run(self):

        while self.running:

            # ---------------------------------------------
            # Pega só o frame mais recente e descarta os
            # atrasados, para não acumular backlog.
            # ---------------------------------------------

            with self.frame_lock:

                frame = self.latest_frame
                frame_time = self.latest_frame_time

                self.latest_frame = None

            if frame is None:

                time.sleep(0.005)

                continue

            with self.detection_lock:

                categories = (
                    None
                    if self.categories is None
                    else set(self.categories)
                )

            # ---------------------------------------------
            # Detector
            # ---------------------------------------------
            #
            # O frame já chega como cópia privada do
            # ScreenCapture, então não copiamos de novo:
            # eram 3 cópias de ~7.8 MB por frame.
            #

            started = time.monotonic()

            try:

                detections = self.detector.detect(
                    frame,
                    categories,
                )

            except Exception as error:

                logger.exception(
                    "Erro no detector: %s",
                    error,
                )

                continue

            self.last_duration = time.monotonic() - started

            self.duration.add(self.last_duration)

            self.rate.tick()

            with self.detection_lock:

                self.detections = detections
                self.detections_time = frame_time

            time.sleep(self.interval)

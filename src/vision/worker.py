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

import numpy as np
import cv2

from core import log
from core.config import VISION_INTERVAL, REFERENCE_WIDTH, REFERENCE_HEIGHT
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

        # Frame que produziu as detecções acima. Não é o
        # latest_frame: aquele já foi substituído.
        self.detections_frame = None

        self.categories = None

        # Escala e offset do letterbox do ultimo frame. None
        # ate' o primeiro set_frame.
        self.latest_norm_transform = None

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

        """
        Recebe o frame bruto (resolução do device) e armazena
        duas versões: a original (para dataset/visualização) e
        uma versão normalizada com letterbox para o detector.
        """

        with self.frame_lock:

            # Frame original
            self.latest_raw_frame = frame

            # Timestamp do instante do frame (pode vir de quem
            # capturou a imagem)
            self.latest_frame_time = (
                time.monotonic() if timestamp is None else timestamp
            )

            # Normaliza preservando proporção com padding (letterbox)
            try:
                src_h, src_w = frame.shape[:2]

                # Tamanho de destino do detector
                dst_w = REFERENCE_WIDTH
                dst_h = REFERENCE_HEIGHT

                # Escala preservando aspecto
                scale = min(dst_w / src_w, dst_h / src_h)

                new_w = int(round(src_w * scale))
                new_h = int(round(src_h * scale))

                resized = cv2.resize(
                    frame,
                    (new_w, new_h),
                    interpolation=cv2.INTER_AREA,
                )

                # Criar canvas preto e centralizar (letterbox)
                canvas = np.zeros((dst_h, dst_w, 3), dtype=resized.dtype)

                x_offset = (dst_w - new_w) // 2
                y_offset = (dst_h - new_h) // 2

                canvas[y_offset : y_offset + new_h, x_offset : x_offset + new_w] = resized

                self.latest_norm_frame = canvas

                # =========================================
                # A TRANSFORMACAO, GUARDADA
                # =========================================
                #
                # Sem ela as deteccoes ficam presas no espaco
                # normalizado, e o unico jeito de voltar para o
                # frame real e' uma regra de tres por
                # REFERENCE_* — que ignora tanto a escala que
                # preserva aspecto quanto as barras do
                # letterbox.
                #
                # Num device 1080x2400 isso passa: escala 1,
                # offset 0. Em qualquer outro aspecto, o offset
                # nao e' zero e o clique sai deslocado por
                # dezenas ou centenas de pixels — sempre na
                # mesma direcao, o que faz parecer erro de
                # deteccao e nao de coordenada.
                self.latest_norm_transform = (
                    scale,
                    x_offset,
                    y_offset,
                )

            except Exception:
                # Em caso de erro, usa o frame original como normalizado
                self.latest_norm_frame = frame

                # Sem letterbox nao ha o que desfazer.
                self.latest_norm_transform = None

            # Limpeza para o consumidor: quando lido, a thread do
            # worker limpa essas referências.

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

    def get_input(self):
        """
        (frame, detecções, instante_do_frame) da última passada.

        É o par imagem+rótulo consistente: get_detections()
        devolve as detecções, mas não a imagem de onde vieram.
        """

        with self.detection_lock:

            return (
                self.detections_frame,
                list(self.detections),
                self.detections_time,
            )

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
    # ESPACO DAS COORDENADAS
    # =====================================================

    @staticmethod
    def _to_raw_space(detections, transform, raw_frame):
        """
        Deteccoes do espaco normalizado -> espaco do frame real.

        Inverte exatamente o que set_frame fez: tira o offset
        das barras do letterbox e desfaz a escala.

        transform None (normalizacao falhou, o detector rodou
        no frame cru) = nada a inverter.
        """

        if not detections or transform is None:
            return detections

        scale, x_offset, y_offset = transform

        if not scale:
            return detections

        if raw_frame is not None:
            altura, largura = raw_frame.shape[:2]
        else:
            altura = largura = None

        convertidas = []

        for deteccao in detections:

            x = (deteccao["x"] - x_offset) / scale
            y = (deteccao["y"] - y_offset) / scale

            largura_caixa = deteccao["width"] / scale
            altura_caixa = deteccao["height"] / scale

            # Uma caixa que cai FORA do frame real veio das
            # barras negras do letterbox. Nao existe objeto ali;
            # tocar nesse ponto e' tocar em nada — ou, pior, no
            # que estiver na borda.
            if largura is not None:

                if x + largura_caixa <= 0 or x >= largura:
                    continue

                if y + altura_caixa <= 0 or y >= altura:
                    continue

            convertida = dict(deteccao)

            convertida["x"] = int(round(x))
            convertida["y"] = int(round(y))
            convertida["width"] = int(round(largura_caixa))
            convertida["height"] = int(round(altura_caixa))

            convertidas.append(convertida)

        return convertidas

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

                raw_frame = getattr(self, "latest_raw_frame", None)
                norm_frame = getattr(self, "latest_norm_frame", None)
                transform = getattr(
                    self,
                    "latest_norm_transform",
                    None,
                )
                frame_time = self.latest_frame_time

                # Limpa para sinalizar que foi consumido
                self.latest_raw_frame = None
                self.latest_norm_frame = None

            if raw_frame is None and norm_frame is None:

                time.sleep(0.005)

                continue

            # Preferir norm_frame para a detecção; se não houver,
            # cai para o raw_frame.
            frame_for_detection = norm_frame if norm_frame is not None else raw_frame

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
            # Usar frame normalizado para a detecção, e guardar o
            # raw_frame como a imagem que produziu as detecções
            # (para o dataset).
            #

            started = time.monotonic()

            try:

                detections = self.detector.detect(
                    frame_for_detection,
                    categories,
                )

            except Exception as error:

                logger.exception(
                    "Erro no detector: %s",
                    error,
                )

                continue

            self.last_duration = time.monotonic() - started

            # =============================================
            # DE VOLTA AO FRAME REAL
            # =============================================
            #
            # A COMPARACAO e' relativa (tudo foi medido no
            # espaco de referencia, e e' isso que faz um
            # template valer em N telas). O CLIQUE nao pode
            # ser: ele tem de cair exatamente onde o objeto
            # esta no frame real.
            #
            # Aqui as duas coisas se encontram — e este e' o
            # unico lugar onde a inversao pode acontecer, porque
            # e' o unico que conhece a transformacao usada.
            #
            # Tambem conserta o dataset: a imagem gravada e' o
            # raw_frame, e as caixas agora estao no espaco dela.
            # Antes, em device fora da referencia, gravava
            # imagem e rotulo em espacos diferentes.
            if norm_frame is not None:

                detections = self._to_raw_space(
                    detections,
                    transform,
                    raw_frame,
                )

            self.duration.add(self.last_duration)

            self.rate.tick()

            with self.detection_lock:

                self.detections = detections
                self.detections_time = frame_time

                # O frame que PRODUZIU estas detecções (raw):
                self.detections_frame = raw_frame if raw_frame is not None else frame_for_detection

            time.sleep(self.interval)

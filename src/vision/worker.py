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

        # Buffer do letterbox, reaproveitado entre quadros.
        # Só a thread do worker toca nele.
        self._canvas = None
        self._canvas_box = None

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
        Guarda o frame bruto. NADA de processamento aqui.

        Esta função roda no loop principal, a cada quadro
        capturado (30-60 por segundo). O worker consome ~3 por
        segundo — o resto é descartado.

        Antes a normalização (resize do frame inteiro + canvas
        de 7.8 MB) era feita AQUI, ou seja: 30 a 60 letterboxes
        por segundo para 3 serem usados. 90% a 95% do trabalho
        ia para o lixo, e ia no thread que também precisa manter
        o imshow respondendo e o ESC funcionando.

        Agora normaliza quem consome, no momento de consumir.
        """

        with self.frame_lock:

            self.latest_raw_frame = frame

            self.latest_frame_time = (
                time.monotonic() if timestamp is None else timestamp
            )

    # =====================================================
    # NORMALIZAÇÃO
    # =====================================================

    def _normalize(self, frame):
        """
        Letterbox para o espaço de referência.

        Devolve (canvas, (escala, x_offset, y_offset)).

        O CANVAS É REAPROVEITADO entre quadros. Isso é seguro
        porque normalizar e detectar acontecem na MESMA thread,
        em sequência: quando o próximo quadro sobrescreve o
        buffer, a detecção do anterior já terminou. Alocar 7.8
        MB por quadro só alimentaria o coletor de lixo.

        As detecções saem no espaço deste canvas e voltam ao
        espaço do frame real em _to_raw_space — é por isso que a
        transformação precisa ser devolvida junto.
        """

        try:

            src_h, src_w = frame.shape[:2]

            dst_w = REFERENCE_WIDTH
            dst_h = REFERENCE_HEIGHT

            escala = min(dst_w / src_w, dst_h / src_h)

            new_w = int(round(src_w * escala))
            new_h = int(round(src_h * escala))

            x_offset = (dst_w - new_w) // 2
            y_offset = (dst_h - new_h) // 2

            # Já está no espaço de referência: devolve como
            # está. Cobre o device 1080x2400, que é o caso
            # comum, e economiza um resize inútil por quadro.
            if (
                new_w == src_w
                and new_h == src_h
                and (dst_w, dst_h) == (src_w, src_h)
            ):
                return frame, (1.0, 0, 0)

            if (
                self._canvas is None
                or self._canvas.shape[:2] != (dst_h, dst_w)
                or self._canvas.dtype != frame.dtype
            ):

                self._canvas = np.zeros(
                    (dst_h, dst_w, 3),
                    dtype=frame.dtype,
                )

                self._canvas_box = None

            # As barras só precisam ser pintadas quando MUDAM de
            # tamanho. Zerar 7.8 MB por quadro para reescrever a
            # mesma faixa preta é trabalho puro.
            box = (x_offset, y_offset, new_w, new_h)

            if box != self._canvas_box:

                self._canvas[:] = 0

                self._canvas_box = box

            cv2.resize(
                frame,
                (new_w, new_h),

                # dst= evita a alocação do resultado: escreve
                # direto na fatia do canvas.
                dst=self._canvas[
                    y_offset:y_offset + new_h,
                    x_offset:x_offset + new_w,
                ],

                interpolation=cv2.INTER_AREA,
            )

            return self._canvas, (escala, x_offset, y_offset)

        except Exception:

            logger.exception(
                "Falha ao normalizar o frame — usando o frame "
                "cru. As coordenadas continuam certas; o que "
                "muda é a escala vista pelo detector."
            )

            return frame, None

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

                raw_frame = self.latest_raw_frame
                frame_time = self.latest_frame_time

                # Limpa para sinalizar que foi consumido
                self.latest_raw_frame = None

            if raw_frame is None:

                time.sleep(0.005)

                continue

            # Aqui, e não no set_frame: só o frame que vai ser
            # DE FATO analisado paga o custo do letterbox.
            frame_for_detection, transform = self._normalize(
                raw_frame
            )

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

                # O detector não tem como saber que o frame
                # passou por resize — quem redimensionou foi
                # este worker. Sem contar, os thresholds
                # calibrados em frame nativo cortam tudo numa
                # tela de outra resolução.
                escala = (
                    transform[0] if transform else 1.0
                )

                detections = self.detector.detect(
                    frame_for_detection,
                    categories,
                    resampled=abs(escala - 1.0) > 0.01,
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
                self.detections_frame = raw_frame

            time.sleep(self.interval)

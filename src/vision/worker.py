"""
Roda o detector numa thread separada.

Mudanças em relação à versão anterior:

1. As detecções carregam o TIMESTAMP do frame que as
   gerou. Antes o loop principal entregava um frame e lia
   o resultado do frame anterior sem saber a defasagem —
   e clicava em coordenada velha achando que era atual.

2. O worker aceita um FILTRO DE CATEGORIAS. Dentro da tela
   de upgrade só interessam 2 categorias, não 43.

3. NADA MATA ESTA THREAD.
   Duas falhas estavam derrubando o worker na PRIMEIRA volta,
   e as duas tinham o mesmo sintoma: bot parado, HUD sem
   detecção nenhuma, nenhum erro visível depois do start.

     - `latest_raw_frame` só existia depois do primeiro
       set_frame, e o _run lia esse atributo antes disso:
       AttributeError na primeira volta, thread morta.

     - o detect era chamado com um argumento `resampled=`
       que o Detector nunca aceitou: TypeError em toda
       passada.

   Agora o corpo do laço é protegido por inteiro, o erro é
   logado uma vez por tipo (e não 60x por segundo), e
   is_alive() deixa o loop principal ver que a visão caiu.

4. SAI O LETTERBOX.
   Normalizar o FRAME para o espaço de referência custava um
   canvas de 7.8 MB e um resize por passada, exigia inverter
   as coordenadas depois, e numa tela deitada encolhia a
   imagem a ~0.45 — o que derrubava toda confiança abaixo do
   threshold. Quem se adapta à resolução agora é o Detector,
   reescalando os TEMPLATES uma vez por resolução de frame.
   As detecções já nascem no espaço do frame real.

5. ESPERA BLOQUEANTE.
   Era um sleep(0.005) em laço: 200 acordadas por segundo
   para, na maioria, não achar frame novo. Agora dorme numa
   Condition e acorda no frame.
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

        # O frame à espera de análise.
        #
        # INICIALIZADO AQUI. Não é detalhe: o _run lê este
        # atributo na primeira volta, antes de qualquer
        # set_frame, e sem esta linha o que acontecia era
        # AttributeError — a thread da visão morria no start e
        # o bot passava a sessão inteira sem uma detecção.
        self.latest_raw_frame = None
        self.latest_frame_time = 0.0

        self.detections = []
        self.detections_time = 0.0

        # Frame que produziu as detecções acima. Não é o
        # latest_frame: aquele já foi substituído.
        self.detections_frame = None

        self.categories = None

        # Um frame novo acorda o worker. Substitui o
        # sleep(0.005) em laço, que acordava 200 vezes por
        # segundo para na maioria não achar nada.
        self.frame_lock = threading.Condition()

        self.detection_lock = threading.Lock()

        # Última falha da thread, para o loop principal e o
        # HUD poderem dizer POR QUE não há detecção.
        self.last_error = None
        self._error_kinds = set()

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

        Agora nem o letterbox existe: quem se adapta à
        resolução é o Detector, reescalando os templates uma
        vez por resolução de frame.
        """

        with self.frame_lock:

            self.latest_raw_frame = frame

            self.latest_frame_time = (
                time.monotonic() if timestamp is None else timestamp
            )

            self.frame_lock.notify()

    # =====================================================
    # CATEGORIAS
    # =====================================================

    def set_categories(self, categories):
        """
        Restringe a busca. None = todas.

        A ORDEM é preservada (tupla, não conjunto): ela é a
        prioridade das regras do estado, e é o que deixa o
        detector parar de procurar na primeira categoria que
        encontrar. Um `set` aqui embaralhava isso em silêncio.
        """

        with self.detection_lock:

            self.categories = (
                None
                if categories is None
                else tuple(categories)
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
    # SAÚDE
    # =====================================================

    def is_alive(self):
        """
        A thread da visão está de pé?

        Existe porque a falha silenciosa era exatamente esta:
        a thread morria no start e o resto do programa
        continuava rodando como se estivesse tudo bem — janela
        aberta, captura a 60 fps, e nenhuma detecção nunca.
        """

        return bool(
            self.running
            and self.thread is not None
            and self.thread.is_alive()
        )

    def _fail(self, error):
        """
        Loga a falha UMA vez por tipo.

        O laço roda várias vezes por segundo: sem isto, um erro
        de programação (um argumento errado, por exemplo) enche
        o terminal de tracebacks idênticos e esconde o resto.
        """

        self.last_error = f"{type(error).__name__}: {error}"

        if type(error).__name__ in self._error_kinds:
            return

        self._error_kinds.add(type(error).__name__)

        logger.exception(
            "Falha na passada do detector — %s. A thread "
            "continua; esta mensagem não repete.",
            self.last_error,
        )

    # =====================================================
    # WORKER
    # =====================================================

    def _run(self):
        """
        O corpo INTEIRO é protegido.

        Qualquer exceção aqui deixava o bot parado sem sinal
        nenhum: sem detecção não há ação, e o log do start já
        tinha subido na tela. Errar e continuar é melhor do que
        morrer em silêncio.
        """

        while self.running:

            try:

                self._pass()

            except Exception as error:

                self._fail(error)

                # Sem isto, um erro imediato viraria laço
                # quente consumindo uma CPU inteira.
                time.sleep(0.1)

    def _pass(self):
        """
        Uma passada: espera frame novo, detecta, publica.
        """

        # -------------------------------------------------
        # Espera o frame mais recente
        # -------------------------------------------------
        #
        # Só o mais novo interessa: os atrasados são
        # descartados, senão a fila cresce e a detecção
        # envelhece — e detecção velha não vira clique.
        with self.frame_lock:

            if self.latest_raw_frame is None:

                # Dorme até chegar frame. O timeout é só para
                # o self.running voltar a ser consultado.
                self.frame_lock.wait(0.2)

            raw_frame = self.latest_raw_frame
            frame_time = self.latest_frame_time

            # Limpa para sinalizar que foi consumido.
            self.latest_raw_frame = None

        if raw_frame is None:
            return

        with self.detection_lock:

            categories = (
                None
                if self.categories is None
                # Tupla: a ordem É a prioridade.
                else self.categories
            )

        # -------------------------------------------------
        # Detector
        # -------------------------------------------------
        #
        # No frame NATIVO: o Detector reescala os templates
        # para a resolução do frame, então as detecções já
        # saem no espaço em que o clique precisa delas. Não há
        # mais transformação para inverter — era ali que
        # imagem e rótulo do dataset saíam de sincronia.

        started = time.monotonic()

        detections = self.detector.detect(
            raw_frame,
            categories,
        )

        self.last_duration = time.monotonic() - started

        self.duration.add(self.last_duration)

        self.rate.tick()

        with self.detection_lock:

            self.detections = detections
            self.detections_time = frame_time

            # O frame que PRODUZIU estas detecções.
            self.detections_frame = raw_frame

        # Só para não monopolizar a CPU quando a passada for
        # muito rápida (poucas categorias).
        if self.interval:

            time.sleep(self.interval)

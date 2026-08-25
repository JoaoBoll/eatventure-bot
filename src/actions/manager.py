"""
Tradução de "ação" em toque no device.

Duas mudanças estruturais em relação à versão anterior:

1. Despacho por TABELA, não por cadeia de if/elif.
   Todas as ações que só clicam no centro da detecção
   apontam para o mesmo handler — o clique é escrito
   uma vez, num lugar só.

2. Execução ASSÍNCRONA, numa thread própria.
   O long press do upgrade de comida dura 4 segundos.
   Antes isso congelava o loop principal inteiro: nenhum
   frame consumido, ESC sem resposta. Agora a ação corre
   em paralelo e a StateMachine consulta is_busy().
"""

import queue
import threading
import time

from actions.android import AndroidActions
from core import log
from core.config import (
    ACTION_POINTS,
    DISMISS_HOLD_DURATION,
    DISMISS_POINT,
    SCROLL_BOTTOM_DIRECTION,
    SCROLL_BOTTOM_SWIPES,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    SWIPE_DISTANCE,
    SWIPE_DURATION_MS,
    SWIPE_X,
    SWIPE_Y,
    UPGRADE_FOOD_PRESS,
    UPGRADE_ITEM_CLICKS,
)

logger = log.get("action")


# =========================================================
# TIPOS DE AÇÃO
# =========================================================

CLICK = "click"        # um toque no centro da detecção
REPEAT = "repeat"      # vários toques no centro da detecção
PRESS = "press"        # toque longo no centro da detecção
DISMISS = "dismiss"    # tap em ponto fixo, ignora a detecção
HOLD = "hold"          # toque MANTIDO em ponto fixo
SCROLL = "scroll"      # rola a tela até o fim, para escapar

# Comportamentos que não precisam de detecção: agem em ponto
# fixo ou não usam coordenada nenhuma.
SEM_DETECCAO = {DISMISS, HOLD, SCROLL}


# =========================================================
# TABELA DE AÇÕES
# =========================================================
#
# Antes eram 20 ramos de if/elif chamando seis métodos
# (_food, _upgrade, _plane, _close, _new_point, _open_box)
# que faziam exatamente a mesma coisa: android.click().
#
# Aqui cada nome aponta para o COMPORTAMENTO. Nomes com o
# mesmo comportamento compartilham o mesmo handler, então
# o clique existe uma vez só e não há duplicata para
# manter em sincronia.
#

ACTION_TABLE = {
    "click": CLICK,
    "food": CLICK,
    "plane": CLICK,
    "upgrade": CLICK,
    "close": CLICK,
    "open_box": CLICK,
    "new_point": CLICK,
    "new_point_click": CLICK,
    "open_renovate": CLICK,
    "renovate_click": CLICK,
    "open_store_click": CLICK,

    "upgrade_item": REPEAT,

    "upgrade_food": PRESS,

    # Fechar painel que abriu sem querer, tocando num ponto
    # neutro. Dois gatilhos com alvo igual e DURAÇÃO
    # diferente: o gray_max fecha com tap, o painel de comida
    # precisa de toque mantido ou não registra.
    "gray_max": DISMISS,
    "dismiss": HOLD,

    # Mesmo comportamento do gray_max — tap num ponto fixo,
    # ignorando onde a detecção apareceu — em OUTRO ponto
    # (GRAY_COIN_POINT). O ponto de cada ação sai de
    # ACTION_POINTS, então uma dispensa nova é uma linha no
    # config e uma aqui, sem tocar no despacho.
    "gray_coin": DISMISS,

    # Rola a tela até o fim. Não fecha nada por si — serve para
    # chegar na posição de rolagem em que o canto de baixo fica
    # vazio, e aí o toque no ponto finalmente fecha em vez de
    # abrir outra coisa.
    #
    # NÃO existe ação "back" aqui de propósito: neste jogo o
    # botão voltar do Android SAI DO JOGO. O android.back()
    # continua implementado, mas fora da tabela.
    "scroll_bottom": SCROLL,
}


def ponto_fixo(action):
    """
    O ponto que esta ação toca, em coordenadas de REFERÊNCIA.

    Só vale para as ações que ignoram a detecção (DISMISS/HOLD).
    Ação sem ponto próprio usa o DISMISS_POINT, que era o único
    que existia antes de haver mais de uma dispensa.

    Função de módulo, e não método, porque as duas leitoras são
    o despacho (que converte para o device) e o rótulo do
    dataset (que converte para o frame) — as duas precisam sair
    do MESMO lugar, ou o dataset grava um ponto e o dedo toca
    outro.
    """

    return ACTION_POINTS.get(action, DISMISS_POINT)


class ActionManager:

    def __init__(self, serial=None):

        self.android = AndroidActions(serial)

        # -------------------------------------------------
        # ESCALA FRAME -> DEVICE
        # -------------------------------------------------
        #
        # As detecções vêm em coordenada do frame decodificado
        # do scrcpy. O toque vai em coordenada do device.
        #
        # Se o stream vier reduzido, as duas coisas são
        # diferentes e o clique cai no lugar errado. Aqui
        # elas ficam explicitamente separadas.
        #

        self.device_size = None
        self.frame_size = None

        # -------------------------------------------------
        # THREAD DE AÇÃO
        # -------------------------------------------------

        self.queue = queue.Queue(maxsize=1)

        self.running = False
        self.thread = None

        self.busy = False
        self.busy_lock = threading.Lock()

        # -------------------------------------------------
        # QUANDO A ÚLTIMA AÇÃO TERMINOU
        # -------------------------------------------------
        #
        # As ações são assíncronas e algumas são LONGAS: o swipe
        # leva SWIPE_DURATION_MS, o long press de comida leva
        # UPGRADE_FOOD_PRESS (4 s). A StateMachine só conhecia o
        # instante da SUBMISSÃO.
        #
        # A diferença importa porque a espera pelo efeito
        # (ACTION_SETTLE / SWIPE_WAITING_TIME) é contada a
        # partir daqui. Medindo da submissão, um settle de 0.4 s
        # já estava vencido quando um press de 4 s terminava — o
        # bot agia sobre um frame capturado no MEIO da ação
        # anterior.
        self.last_finished_at = 0.0

    # =====================================================
    # SETUP
    # =====================================================

    def start(self):

        if self.running:
            return

        self.device_size = self.android.get_screen_size()

        if self.device_size:

            logger.info(
                "Device: %dx%d",
                self.device_size[0],
                self.device_size[1],
            )

        else:

            self.device_size = (
                REFERENCE_WIDTH,
                REFERENCE_HEIGHT,
            )

            logger.warning(
                "Resolução do device desconhecida, "
                "assumindo %dx%d",
                REFERENCE_WIDTH,
                REFERENCE_HEIGHT,
            )

        self.running = True

        self.thread = threading.Thread(
            target=self._run,
            name="action-worker",
            daemon=True,
        )

        self.thread.start()

    def stop(self):

        self.running = False

        # Destrava o worker se estiver esperando na fila.
        try:
            self.queue.put_nowait(None)

        except queue.Full:
            pass

        if self.thread:

            self.thread.join(
                timeout=UPGRADE_FOOD_PRESS + 3.0
            )

            self.thread = None

    def set_frame_size(self, width, height):
        """
        Informa em que resolução as detecções estão sendo
        calculadas. Chamado pelo loop principal.
        """

        size = (width, height)

        if size == self.frame_size:
            return

        self.frame_size = size

        if self.device_size and size != self.device_size:

            logger.warning(
                "Frame %dx%d != device %dx%d — "
                "convertendo coordenadas de toque.",
                width,
                height,
                self.device_size[0],
                self.device_size[1],
            )

    # =====================================================
    # CONVERSÃO DE COORDENADAS
    # =====================================================

    def _from_frame(self, x, y):
        """
        Coordenada do frame -> coordenada do device.
        """

        if not self.frame_size or not self.device_size:
            return int(x), int(y)

        frame_width, frame_height = self.frame_size
        device_width, device_height = self.device_size

        if not frame_width or not frame_height:
            return int(x), int(y)

        return (
            int(round(x * device_width / frame_width)),
            int(round(y * device_height / frame_height)),
        )

    def _from_reference(self, x, y):
        """
        Coordenada da resolução de referência (onde os
        pontos fixos foram anotados) -> device.
        """

        if not self.device_size:
            return int(x), int(y)

        device_width, device_height = self.device_size

        return (
            int(round(x * device_width / REFERENCE_WIDTH)),
            int(round(y * device_height / REFERENCE_HEIGHT)),
        )

    def _reference_to_frame(self, x, y):
        """
        Coordenada da resolução de referência -> FRAME.

        Existe para o rótulo do dataset: a imagem gravada é o
        frame, então o ponto tocado tem de estar no espaço
        dele. Não é usada para tocar.
        """

        if not self.frame_size:
            return int(x), int(y)

        frame_width, frame_height = self.frame_size

        return (
            int(round(x * frame_width / REFERENCE_WIDTH)),
            int(round(y * frame_height / REFERENCE_HEIGHT)),
        )

    def target_frame(self, action, detection):
        """
        Onde esta ação vai tocar, em coordenada de FRAME —
        o mesmo espaço da imagem que o dataset grava.

        None quando a ação não tem alvo pontual (scroll).

        Deliberadamente separada de _dispatch: o caminho do
        toque continua convertendo direto da referência para o
        device, sem passar pelo frame, porque dupla conversão
        introduz arredondamento e é aquele caminho que gasta
        moeda. As duas leem a MESMA constante, e
        tests/test_dataset.py confere que caem no mesmo ponto.
        """

        kind = ACTION_TABLE.get(action)

        if kind is None or kind == SCROLL:
            return None

        if kind in (DISMISS, HOLD):

            return self._reference_to_frame(
                *ponto_fixo(action)
            )

        if detection is None:
            return None

        return (
            detection["x"] + detection["width"] // 2,
            detection["y"] + detection["height"] // 2,
        )

    def kind_of(self, action):
        """
        Comportamento por trás do nome da ação.
        """

        return ACTION_TABLE.get(action)

    # =====================================================
    # SUBMISSÃO
    # =====================================================

    def is_busy(self):

        with self.busy_lock:
            return self.busy

    def execute(self, action, detection):
        """
        Enfileira uma ação. NÃO bloqueia.

        Devolve False se já existe ação em andamento — a
        StateMachine usa isso para não empilhar cliques.
        """

        kind = ACTION_TABLE.get(action)

        if kind is None:

            logger.warning(
                "Ação desconhecida: %s",
                action,
            )

            return False

        if kind not in SEM_DETECCAO and detection is None:
            return False

        return self._submit(
            ("action", action, kind, detection)
        )

    def swipe(self, direction):

        return self._submit(
            ("swipe", direction, None, None)
        )

    def _submit(self, job):

        with self.busy_lock:

            if self.busy:
                return False

            self.busy = True

        try:

            self.queue.put_nowait(job)

        except queue.Full:

            with self.busy_lock:
                self.busy = False

            return False

        return True

    # =====================================================
    # WORKER
    # =====================================================

    def _run(self):

        while self.running:

            try:

                job = self.queue.get(timeout=0.2)

            except queue.Empty:
                continue

            if job is None:
                break

            try:

                self._dispatch(job)

            except Exception as error:

                logger.exception(
                    "Erro executando %s: %s",
                    job,
                    error,
                )

            finally:

                with self.busy_lock:

                    # Antes de liberar o busy: quem acordar
                    # vendo "livre" precisa ver também QUANDO
                    # ficou livre.
                    self.last_finished_at = time.monotonic()

                    self.busy = False

    def _dispatch(self, job):

        job_type, name, kind, detection = job

        if job_type == "swipe":

            self._swipe(name)

            return

        # -------------------------------------------------
        # Centro do objeto detectado
        # -------------------------------------------------

        if detection is not None:

            center_x = (
                detection["x"]
                + detection["width"] // 2
            )

            center_y = (
                detection["y"]
                + detection["height"] // 2
            )

            x, y = self._from_frame(center_x, center_y)

        else:

            x, y = 0, 0

        # -------------------------------------------------
        # Um ponto de saída por comportamento
        # -------------------------------------------------

        if kind == CLICK:

            self._click(name, x, y)

        elif kind == REPEAT:

            self._repeat(name, x, y)

        elif kind == PRESS:

            self._press(name, x, y)

        elif kind == DISMISS:

            x, y = self._from_reference(*ponto_fixo(name))

            self._click(name, x, y)

        elif kind == HOLD:

            x, y = self._from_reference(*ponto_fixo(name))

            self._hold(name, x, y)

        elif kind == SCROLL:

            self._scroll_bottom(name)

    # =====================================================
    # COMPORTAMENTOS
    # =====================================================

    def _click(self, name, x, y):

        logger.info(
            "%s -> click (%d, %d)",
            name,
            x,
            y,
        )

        self.android.click(x, y)

    def _repeat(self, name, x, y):
        """
        Os N cliques num único comando adb.

        Antes: N processos adb com sleeps entre eles, o que
        dava 0.9 a 1.8 s para evoluir um item. O tempo era
        todo overhead de processo — nada disso é o jogo
        precisando de pausa.
        """

        logger.info(
            "%s -> %d clicks (%d, %d)",
            name,
            UPGRADE_ITEM_CLICKS,
            x,
            y,
        )

        self.android.tap_many(x, y, UPGRADE_ITEM_CLICKS)

    def _hold(self, name, x, y):
        """
        Toque mantido num ponto fixo, curto.

        Diferente do _press, que segura por 4 s no centro de
        uma detecção para evoluir comida. Aqui a intenção é só
        garantir que o toque registre.
        """

        logger.info(
            "%s -> hold (%d, %d) por %.2fs",
            name,
            x,
            y,
            DISMISS_HOLD_DURATION,
        )

        self.android.press(
            x,
            y,
            duration=DISMISS_HOLD_DURATION,
        )

    def _press(self, name, x, y):

        logger.info(
            "%s -> press (%d, %d) por %.1fs",
            name,
            x,
            y,
            UPGRADE_FOOD_PRESS,
        )

        self.android.press(
            x,
            y,
            duration=UPGRADE_FOOD_PRESS,
        )

    def _scroll_bottom(self, name):
        """
        Sequência de swipes até o fim da tela.

        Roda na thread de ação, então segurar ~1.5 s aqui não
        congela o loop principal.
        """

        logger.info(
            "%s -> %d swipes '%s' (rolar até o fim)",
            name,
            SCROLL_BOTTOM_SWIPES,
            SCROLL_BOTTOM_DIRECTION,
        )

        # Os N swipes num único comando adb: eram 6
        # processos mais 5 pausas, ~5 s só para rolar a
        # tela.
        if SCROLL_BOTTOM_DIRECTION == "up":
            end_y = SWIPE_Y - SWIPE_DISTANCE

        else:
            end_y = SWIPE_Y + SWIPE_DISTANCE

        start_x, start_y = self._from_reference(
            SWIPE_X,
            SWIPE_Y,
        )

        _, target_y = self._from_reference(SWIPE_X, end_y)

        self.android.swipe_many(
            start_x,
            start_y,
            start_x,
            target_y,
            SCROLL_BOTTOM_SWIPES,
            SWIPE_DURATION_MS,
        )

    # =====================================================
    # SWIPE
    # =====================================================

    def _swipe(self, direction):

        if direction == "up":

            end_y = SWIPE_Y - SWIPE_DISTANCE

        elif direction == "down":

            end_y = SWIPE_Y + SWIPE_DISTANCE

        else:

            logger.warning(
                "Direção de swipe inválida: %s",
                direction,
            )

            return

        start_x, start_y = self._from_reference(
            SWIPE_X,
            SWIPE_Y,
        )

        _, target_y = self._from_reference(
            SWIPE_X,
            end_y,
        )

        logger.info(
            "swipe %s (%d -> %d)",
            direction,
            start_y,
            target_y,
        )

        self.android.swipe(
            start_x,
            start_y,
            start_x,
            target_y,
            SWIPE_DURATION_MS,
        )

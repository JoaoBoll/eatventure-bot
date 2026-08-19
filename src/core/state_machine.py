"""
Máquina de estados do bot.

Mudanças em relação à versão anterior:

1. TABELA DE REGRAS por estado, em vez de cascata de if.
   Cada estado declara "que categoria procuro, em que
   ordem, o que faço e para onde vou". A prioridade fica
   legível numa linha em vez de espalhada em 200.

2. UM ÚNICO ponto de ação (_act).
   Antes cada bloco repetia: checa cooldown, executa,
   atualiza last_action_time, troca de estado. Quatro
   linhas repetidas 12 vezes é onde uma delas fica faltando.

3. TIMEOUT POR ESTADO.
   RENOVATE só saía achando a moeda; UPGRADE só saía achando
   o botão de fechar. Um modal sem template travava o bot
   para sempre, sem nem explorar (o swipe só roda em NORMAL).

4. DETECÇÃO VELHA NÃO VIRA CLIQUE.
   Se o frame que gerou a detecção já tem mais que
   MAX_DETECTION_AGE, o objeto provavelmente saiu do lugar.

5. VARIÁVEIS DE ESTADO ISOLADAS.
   up_food_wait_start era compartilhada entre FOOD e
   NEW_POINT. Agora é limpa na entrada de cada estado.

6. Removido o método swipe() morto, que usava self.android
   (inexistente nesta classe) e duplicava o swipe do
   ActionManager com coordenadas DIFERENTES.
"""

import logging
import time

from core import log
from core.config import (
    ACTION_COOLDOWN,
    DISMISS_ACTIONS,
    DISMISS_ATTEMPTS_BEFORE_SCROLL,
    EXPLORATION_DELAY,
    MAX_DETECTION_AGE,
    MAX_SWIPES,
    REPEATED_ACTION_WARNING,
    STATE_TIMEOUTS,
    SWIPE_START_DIRECTION,
    UP_FOOD_WAIT,
)

logger = log.get("state")


# =========================================================
# ESTADOS
# =========================================================

NORMAL = "NORMAL"
RENOVATE = "RENOVATE"
FOOD = "FOOD"
NEW_POINT = "NEW_POINT"
UPGRADE = "UPGRADE"


# =========================================================
# TABELAS DE REGRAS
# =========================================================
#
# (categoria, ação, próximo estado)
#
# Ordem = prioridade. A primeira regra que encontrar sua
# categoria é executada, e nenhuma outra roda no frame.
#
# next_state None = permanece no estado atual.
#

# As quatro primeiras fecham o que não deveria estar aberto,
# e por isso vêm antes de qualquer ação de jogo.
#
# up_food aparecendo em NORMAL significa que o painel de
# comida abriu sem querer: em NORMAL não existe nada a fazer
# com ele, então dispensa com toque MANTIDO curto num ponto
# neutro (tap seco às vezes não fecha o painel).
#
# Dentro do estado FOOD o MESMO up_food faz o oposto: long
# press de 4 s no próprio botão, para evoluir a comida. É o
# estado que decide o significado da mesma detecção — e as
# duas ações custam coisas bem diferentes, então não confunda:
# dispensar não gasta moeda, evoluir gasta.
NORMAL_RULES = [
    ("open_store", "open_store_click", None),
    ("close", "close", None),
    ("gray_max", "gray_max", None),
    ("up_food", "upgrade_food", None),

    ("plane", "plane", RENOVATE),
    ("build", "click", RENOVATE),
    ("upgrade", "upgrade", UPGRADE),
    ("new_point", "new_point", NEW_POINT),
    ("box", "open_box", None),
    ("food", "food", FOOD),
]

RENOVATE_RULES = [
    ("renovate", "renovate_click", NORMAL),
    ("fly", "renovate_click", NORMAL),
]

# up_upgrade permanece em UPGRADE de propósito: no próximo
# frame procuramos outro item para evoluir. Só saímos da
# tela quando não houver mais nenhum.
UPGRADE_RULES = [
    ("up_upgrade", "upgrade_item", None),
    ("close", "close", NORMAL),
]

STATE_RULES = {
    NORMAL: NORMAL_RULES,
    RENOVATE: RENOVATE_RULES,
    UPGRADE: UPGRADE_RULES,

    # FOOD e NEW_POINT têm espera própria depois do press,
    # então são tratados em handler dedicado. As categorias
    # ficam aqui só para o filtro do detector.
    FOOD: [
        ("up_food", "upgrade_food", None),
        ("gray_max", "gray_max", None),
    ],
    NEW_POINT: [
        ("up_food", "new_point_click", None),
    ],
}


class StateMachine:

    # Mantidos como atributos de classe por compatibilidade
    # com quem referenciava StateMachine.NORMAL.
    NORMAL = NORMAL
    RENOVATE = RENOVATE
    FOOD = FOOD
    NEW_POINT = NEW_POINT
    UPGRADE = UPGRADE

    def __init__(self, action_manager):

        self.action_manager = action_manager

        self.state = NORMAL
        self.state_entered = time.monotonic()

        self.last_action_time = 0.0
        self.action_cooldown = ACTION_COOLDOWN

        # Espera depois do long press de comida.
        self.up_food_wait_start = None

        # =================================================
        # EXPLORAÇÃO DA TELA
        # =================================================

        self.exploration_delay = EXPLORATION_DELAY

        self.last_detection_time = time.monotonic()

        self.swipe_direction = SWIPE_START_DIRECTION
        self.swipe_count = 0
        self.max_swipes = MAX_SWIPES

        # Evita repetir o aviso de detecção velha a cada frame.
        self._stale_warned = 0.0

        # Detecta ação que repete sem resolver nada.
        self._last_action = None
        self._repeat_count = 0

        # Tentativas consecutivas de fechar algo. Sobe a escada
        # de escalonamento em vez de repetir o que não funcionou.
        self._dismiss_attempts = 0

        # Quantas vezes já precisou rolar para escapar.
        self._dismiss_rounds = 0

    # =====================================================
    # CATEGORIAS DE INTERESSE
    # =====================================================

    def wanted_categories(self):
        """
        Categorias que importam no estado atual.

        É o filtro que o detector usa para não procurar 43
        templates quando 2 bastam. Em UPGRADE isso é a
        diferença entre ~320 ms e ~20 ms por passada.
        """

        rules = STATE_RULES.get(self.state, NORMAL_RULES)

        categories = {category for category, _, _ in rules}

        # Fora de NORMAL, ainda queremos ver o "X": é a
        # saída de emergência de qualquer modal.
        categories.add("close")

        return categories

    # =====================================================
    # UPDATE
    # =====================================================

    def update(self, detections, lag=0.0):

        # =================================================
        # TIMEOUT DO ESTADO
        # =================================================

        if self._timed_out():
            return

        # =================================================
        # DETECÇÃO VELHA
        # =================================================
        #
        # Clicar com base num frame antigo acerta onde o
        # objeto ESTAVA.
        #

        if (
            MAX_DETECTION_AGE is not None
            and lag > MAX_DETECTION_AGE
        ):

            now = time.monotonic()

            if now - self._stale_warned > 5.0:

                logger.warning(
                    "Detecções com %.0f ms de atraso "
                    "(máx %.0f ms) — não vou clicar. "
                    "Detector lento demais?",
                    lag * 1000,
                    MAX_DETECTION_AGE * 1000,
                )

                self._stale_warned = now

            return

        # =================================================
        # HANDLER DO ESTADO
        # =================================================

        if self.state == FOOD:

            self._food(detections)

        elif self.state == NEW_POINT:

            self._new_point(detections)

        else:

            acted = self._apply_rules(
                detections,
                STATE_RULES.get(self.state, NORMAL_RULES),
            )

            # Só NORMAL explora: nos outros estados estamos
            # dentro de um painel, e swipe atrapalharia.
            if not acted and self.state == NORMAL:

                self._explore_screen()

    # =====================================================
    # APLICAÇÃO DAS REGRAS
    # =====================================================

    def _apply_rules(self, detections, rules):
        """
        Executa a primeira regra cuja categoria aparecer.
        Devolve True se algo foi encontrado (mesmo que a
        ação tenha sido barrada por cooldown), porque
        encontrar algo significa que não estamos perdidos.
        """

        for prioridade, (category, action, next_state) in enumerate(
            rules,
            start=1,
        ):

            detection = self._find(detections, category)

            if detection is None:
                continue

            # Em DEBUG, mostra qual prioridade venceu e o que
            # estava na tela junto. É o que responde "por que
            # clicou nisso e não naquilo".
            if logger.isEnabledFor(logging.DEBUG):

                na_tela = " ".join(
                    f"{d['category']}({d['confidence']:.2f})"
                    for d in detections
                )

                logger.debug(
                    "%s prio %d/%d: %s -> %s | na tela: %s",
                    self.state,
                    prioridade,
                    len(rules),
                    category,
                    action,
                    na_tela or "nada",
                )

            self._reset_exploration()

            self._act(
                action,
                detection,
                next_state,
            )

            return True

        return False

    # =====================================================
    # AÇÃO
    # =====================================================

    def _act(self, action, detection, next_state=None):
        """
        Único lugar do projeto que dispara ação e troca
        estado.
        """

        if not self._can_act():
            return False

        action = self._escalate(action)

        if not self.action_manager.execute(action, detection):

            # Worker ocupado: tenta no próximo frame.
            return False

        confidence = (
            detection["confidence"]
            if detection
            else 0.0
        )

        logger.info(
            "%s: %s (F=%.3f)",
            self.state,
            action,
            confidence,
        )

        self.last_action_time = time.monotonic()

        self._count_repeat(action)

        if next_state is not None and next_state != self.state:

            self._enter(next_state)

        return True

    def _escalate(self, action):
        """
        Escolhe a tentativa de fechamento conforme quantas já
        falharam.

        Existe porque o ponto fixo pode ser justamente o que
        ABRE o painel: aí tocar nele para fechar reabre, e o
        ciclo não termina sozinho.

        A escada é: N tentativas no ponto, depois rolar a tela
        até o fim (onde o canto de baixo fica vazio), e tentar o
        ponto outra vez. O ciclo se repete, porque não há saída
        melhor — o BACK do Android sai do jogo neste jogo.
        """

        if action not in DISMISS_ACTIONS:

            # Ação normal: progresso, zera a escada.
            self._dismiss_attempts = 0
            self._dismiss_rounds = 0

            return action

        self._dismiss_attempts += 1

        if (
            self._dismiss_attempts
            <= DISMISS_ATTEMPTS_BEFORE_SCROLL
        ):
            return action

        # Estourou as tentativas no ponto: rola e reinicia a
        # contagem, para tentar o ponto de novo depois.
        self._dismiss_attempts = 0
        self._dismiss_rounds += 1

        logger.warning(
            "'%s' não fechou em %d tentativas (rodada %d) — "
            "rolando a tela até o fim para achar posição onde "
            "o ponto seja seguro",
            action,
            DISMISS_ATTEMPTS_BEFORE_SCROLL,
            self._dismiss_rounds,
        )

        return "scroll_bottom"

    def _count_repeat(self, action):
        """
        Avisa quando a mesma ação repete sem levar a nada.

        Sintoma típico: o ponto de dispensa não fecha o painel,
        e o bot fica tocando o canto da tela indefinidamente.
        """

        if action != self._last_action:

            self._last_action = action
            self._repeat_count = 1

            return

        self._repeat_count += 1

        if self._repeat_count % REPEATED_ACTION_WARNING:
            return

        logger.warning(
            "'%s' repetiu %d vezes seguidas em %s sem "
            "resolver. Ponto de dispensa errado, ou falta "
            "template para o que está na tela?",
            action,
            self._repeat_count,
            self.state,
        )

    def _can_act(self):

        # Ação em andamento (ex.: long press de 4 s) precisa
        # terminar antes da próxima.
        if self.action_manager.is_busy():
            return False

        return (
            time.monotonic() - self.last_action_time
            >= self.action_cooldown
        )

    # =====================================================
    # TROCA DE ESTADO
    # =====================================================

    def _enter(self, state):

        if state == self.state:
            return

        logger.info(
            "%s -> %s",
            self.state,
            state,
        )

        self.state = state
        self.state_entered = time.monotonic()

        # Variáveis locais do estado anterior não vazam
        # para o próximo.
        self.up_food_wait_start = None

        # Trocar de estado é progresso: as contagens zeram.
        self._last_action = None
        self._repeat_count = 0
        self._dismiss_attempts = 0
        self._dismiss_rounds = 0

        self._reset_exploration()

    def _timed_out(self):

        timeout = STATE_TIMEOUTS.get(self.state)

        if timeout is None:
            return False

        elapsed = time.monotonic() - self.state_entered

        if elapsed < timeout:
            return False

        logger.warning(
            "%s travado por %.1fs — voltando para NORMAL",
            self.state,
            elapsed,
        )

        self._enter(NORMAL)

        return True

    # =====================================================
    # FOOD
    # =====================================================

    def _food(self, detections):
        """
        Segura o botão de upgrade de comida, e espera o
        próximo aparecer. Sem próximo em UP_FOOD_WAIT,
        volta para NORMAL.
        """

        up_food = self._find(detections, "up_food")

        if up_food is not None:

            self._reset_exploration()

            if self._act("upgrade_food", up_food):

                self.up_food_wait_start = time.monotonic()

            return

        # Painel de upgrade esgotado: dispensa.
        gray_max = self._find(detections, "gray_max")

        if gray_max is not None:

            self._reset_exploration()

            self._act("gray_max", gray_max, NORMAL)

            return

        self._wait_or_give_up()

    # =====================================================
    # NEW POINT
    # =====================================================

    def _new_point(self, detections):
        """
        Igual ao FOOD, mas aqui basta um clique para
        liberar — não é long press.
        """

        unlock_food = self._find(detections, "up_food")

        if unlock_food is not None:

            self._reset_exploration()

            if self._act("new_point_click", unlock_food):

                self.up_food_wait_start = time.monotonic()

            return

        self._wait_or_give_up()

    def _wait_or_give_up(self):
        """
        Depois de agir, a tela pode demorar para atualizar.
        Espera UP_FOOD_WAIT antes de desistir.
        """

        if self.up_food_wait_start is None:
            return

        elapsed = time.monotonic() - self.up_food_wait_start

        if elapsed < UP_FOOD_WAIT:
            return

        logger.info(
            "Nenhum up_food em %.1fs",
            UP_FOOD_WAIT,
        )

        self._enter(NORMAL)

    # =====================================================
    # FIND
    # =====================================================

    def _find(self, detections, category):
        """
        Primeira detecção da categoria.

        O detector já devolve ordenado por confiança e já
        aplicou o threshold da categoria, então aqui não há
        mais refiltragem: antes o threshold existia em dois
        lugares, com valores diferentes (upgrade era 0.98 no
        detector e 0.80 aqui), e o resultado escolhido era o
        primeiro em ordem alfabética de arquivo, não o melhor.
        """

        for detection in detections:

            if detection["category"] == category:
                return detection

        return None

    # =====================================================
    # RESET
    # =====================================================

    def reset(self):

        self._enter(NORMAL)

        self.last_action_time = 0.0

    # =====================================================
    # EXPLORAÇÃO
    # =====================================================

    def _reset_exploration(self):

        self.last_detection_time = time.monotonic()

        self.swipe_count = 0

    def _explore_screen(self):

        now = time.monotonic()

        if (
            now - self.last_detection_time
            < self.exploration_delay
        ):
            return

        if not self._can_act():
            return

        logger.info(
            "explore: swipe %s %d/%d",
            self.swipe_direction,
            self.swipe_count + 1,
            self.max_swipes,
        )

        if not self.action_manager.swipe(
            self.swipe_direction
        ):
            return

        self.last_action_time = now
        self.last_detection_time = now

        self.swipe_count += 1

        # =================================================
        # CHEGOU NO LIMITE?
        # =================================================

        if self.swipe_count >= self.max_swipes:

            self.swipe_direction = (
                "down"
                if self.swipe_direction == "up"
                else "up"
            )

            self.swipe_count = 0

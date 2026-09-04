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
from collections import Counter

from core import log
from core.metrics import formata_duracao
from core.config import (
    ACTION_COOLDOWN,
    ACTION_SETTLE,
    CYCLE_CATEGORIES,
    DISMISS_ACTIONS,
    DISMISS_ATTEMPTS_BEFORE_SCROLL,
    EXPLORATION_DELAY,
    EXPLORATION_DELAY_AFTER_ACTION,
    MAX_DETECTION_AGE,
    MAX_SWIPES,
    REPEATED_ACTION_WARNING,
    STATE_ENTRY_SETTLE,
    STATE_TIMEOUTS,
    SWIPE_START_DIRECTION,
    SWIPE_WAIT_FOR_NO_ACTION,
    SWIPE_WAITING_TIME,
    UP_FOOD_WAIT,
    VIEW_MOVING_ACTIONS,
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
# up_food em NORMAL faz o MESMO que em FOOD: long press de
# UPGRADE_FOOD_PRESS segundos no botão, evoluindo a comida.
#
# ESCOLHA DELIBERADA, contra a alternativa de dispensar o
# painel. Vale saber o que ela custa: o painel de comida às
# vezes abre sem querer, e nesse caso o bot gasta moeda e fica
# travado o tempo do press.
#
# Consequência menos óbvia: "upgrade_food" não está em
# DISMISS_ACTIONS, então a escada de fechamento (tenta N vezes
# -> rola a tela) NÃO vale aqui. up_food preso em NORMAL repete
# o press indefinidamente; o que avisa é REPEATED_ACTION_WARNING
# no log.
#
# Para voltar a dispensar, troque a ação por "dismiss": ela
# continua implementada e coberta por
# tests/test_pipeline.py::test_dismiss_toca_no_ponto_neutro.
NORMAL_RULES = [
    ("open_store", "open_store_click", None),
    ("close", "close", None),
    ("gray_max", "gray_max", None),
    ("gray_coin", "gray_coin", None),
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
        ("gray_coin", "gray_coin", None)
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

    def __init__(self, action_manager, recorder=None):

        self.action_manager = action_manager

        # Gravador do dataset de treino. None = não grava.
        self.recorder = recorder

        self.state = NORMAL
        self.state_entered = time.monotonic()

        self.last_action_time = 0.0
        self.action_cooldown = ACTION_COOLDOWN
        self.action_settle = ACTION_SETTLE

        # Espera própria do swipe, maior que a de um toque: um
        # toque mexe um painel, um swipe move a VISTA INTEIRA e
        # o jogo segue deslizando por inércia.
        self.swipe_settle = SWIPE_WAITING_TIME

        # A última ação foi um swipe?
        #
        # Guardado como BANDEIRA, e não como uma cópia do valor
        # do settle, de propósito: assim `machine.action_settle
        # = 0` continua desligando a espera de toque (é o que os
        # testes fazem) sem que exista um segundo número
        # desatualizado em algum lugar.
        self._last_was_swipe = False

        # Frame atual, guardado para o gravador do dataset.
        self._frame = None
        self._detections = []
        self._lag = 0.0

        # Quando o frame que gerou as detecções em mão foi
        # CAPTURADO (não quando chegou aqui).
        #
        # Sem isso o bot age duas vezes sobre a mesma tela: o
        # cooldown libera antes de existir um frame que mostre
        # o efeito da primeira ação. Ver _can_act.
        self._frame_time = None

        # Quantas vezes esperou frame novo, para não encher o
        # log com uma linha por quadro.
        self._waited_warned = 0.0

        # Espera depois do long press de comida.
        self.up_food_wait_start = None

        # =================================================
        # TEMPO CORRIDO ENTRE REFORMAS
        # =================================================
        #
        # Começa a contar do start, não do primeiro build:
        # o primeiro trecho também é informação ("já são 8
        # minutos e ele ainda não passou de restaurante").
        #
        self.cycle_at = time.monotonic()
        self.cycle_last = None
        self.cycle_count = 0

        # =================================================
        # O QUE O BOT FEZ, EM NÚMERO
        # =================================================
        #
        # Contado em _act, o único ponto do projeto por onde
        # ação sai. Contar em _apply_rules incluiria tentativa
        # barrada por cooldown ou worker ocupado — e aí o número
        # diria quantas vezes o bot QUIS agir, não quantas agiu.
        #
        # Por categoria da detecção e por nome da ação, porque
        # as duas perguntas são diferentes: `fly` é categoria
        # (quantas vezes voou), `upgrade_item` é ação (quantos
        # cliques de upgrade).
        self.category_counts = Counter()
        self.action_counts = Counter()

        self.last_action = None
        self.last_action_at = None

        # =================================================
        # EXPLORAÇÃO DA TELA
        # =================================================

        # Ritmo normal: um swipe a cada tanto, enquanto não
        # aparecer nada.
        self.exploration_delay = EXPLORATION_DELAY

        # Depois de ACHAR algo, a exploração é ADIADA por este
        # tanto — não cancelada.
        self.exploration_action_delay = (
            EXPLORATION_DELAY_AFTER_ACTION
        )

        # De quando se conta a espera do próximo swipe: o
        # último swipe, ou a última vez que algo foi achado.
        self.explore_anchor = time.monotonic()

        # O âncora veio de um "achou"?
        #
        # Guardado como BANDEIRA, e o intervalo derivado dela na
        # hora — não uma cópia do número. É o que mantém
        # `machine.exploration_delay = 0` funcionando (os testes
        # usam isso) sem existir um segundo valor guardado que
        # possa ficar desatualizado. Mesmo padrão do
        # _last_was_swipe.
        self.explore_found = False

        # =================================================
        # O CICLO DE VARREDURA
        # =================================================
        #
        # 5 swipes para um lado, 5 para o outro, indefinidamente.
        #
        # A contagem NÃO zera quando o bot acha algo. Zerava
        # antes, e o efeito era o bot varrer sempre o mesmo
        # pedaço da tela: como quase todo swipe revela algum
        # alvo, a contagem voltava para 1 antes de a volta
        # fechar, e a metade distante do restaurante nunca era
        # visitada.
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
        Categorias que importam no estado atual, NA ORDEM DE
        PRIORIDADE.

        É o filtro que o detector usa para não procurar 185
        templates quando 4 bastam. Em UPGRADE isso é a
        diferença entre ~264 ms e ~16 ms por passada.

        A ORDEM é significativa, e é por isso que isto devolve
        lista e não conjunto: as regras são avaliadas de cima
        para baixo e a primeira que casar é a que age, então o
        detector pode parar de procurar na primeira categoria
        que encontrar (VISION_PRIORITY_STOP). `food`, a última
        prioridade em NORMAL, é sozinha 2/3 dos templates.
        """

        rules = STATE_RULES.get(self.state, NORMAL_RULES)

        categories = []

        for category, _, _ in rules:

            if category not in categories:

                categories.append(category)

        # Fora de NORMAL, ainda queremos ver o "X": é a
        # saída de emergência de qualquer modal.
        #
        # No FIM da lista: é rede de segurança, não prioridade.
        # Nos estados em que o "X" importa de verdade ele já
        # está nas regras, na posição certa.
        if "close" not in categories:

            categories.append("close")

        return categories

    # =====================================================
    # UPDATE
    # =====================================================

    def update(
        self,
        detections,
        lag=0.0,
        frame=None,
        frame_time=None,
    ):

        # =================================================
        # DATASET
        # =================================================
        #
        # Antes de qualquer return: é aqui que a ação anterior
        # recebe veredito, e ela precisa disso mesmo nos frames
        # em que o bot não age.
        #
        if self.recorder is not None:

            self._frame = frame

            self._detections = detections

            self.recorder.observe(
                frame,
                detections,
                frame_time,
                self.state,
            )

        # =================================================
        # QUANDO ESTA TELA FOI VISTA
        # =================================================
        #
        # lag é a idade do frame que gerou estas detecções, e
        # é o que permite saber se elas já poderiam refletir a
        # última ação. Guardado antes de qualquer coisa: todo
        # caminho de ação passa por _can_act.
        #
        self._frame_time = time.monotonic() - lag

        self._lag = lag

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
            if self.state == NORMAL and (
                not SWIPE_WAIT_FOR_NO_ACTION or not acted
            ):

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

            self._delay_exploration()

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

        # `scroll_bottom` é seis swipes seguidos: move a vista
        # mais que qualquer swipe solto, e por isso merece a
        # espera longa igual. Um toque comum fica na curta.
        self._last_was_swipe = action in VIEW_MOVING_ACTIONS

        # Mesmo raciocínio do ciclo, abaixo: só conta o que
        # realmente saiu.
        self.action_counts[action] += 1

        if detection:
            self.category_counts[detection["category"]] += 1

        self.last_action = action
        self.last_action_at = self.last_action_time

        # Aqui, e não em _apply_rules: este é o ponto em que a
        # ação SAIU de verdade. Marcar antes contaria ciclo em
        # tentativa barrada por cooldown ou worker ocupado.
        if detection and detection["category"] in CYCLE_CATEGORIES:

            self._mark_cycle(detection["category"])

        self._count_repeat(action)

        self._record(action, detection)

        if next_state is not None and next_state != self.state:

            self._enter(next_state)

        return True

    # =====================================================
    # DATASET
    # =====================================================

    def _record(self, action, detection):
        """
        Entrega ao gravador a ação que ACABOU de sair, com o
        frame e os rótulos que a motivaram.

        Chamado de dentro de _act de propósito: é o único
        ponto em que se sabe que a ação saiu de verdade, e não
        foi barrada por cooldown ou por worker ocupado.
        """

        if self.recorder is None:
            return

        if self._frame is None:
            return

        self.recorder.on_action(
            action=action,

            # swipe_* não está em ACTION_TABLE: é a exploração,
            # que chama o ActionManager por outro caminho.
            action_kind=(
                "swipe"
                if action.startswith("swipe_")
                else self.action_manager.kind_of(action)
            ),
            detection=detection,

            # Ponto tocado no espaço da IMAGEM gravada.
            # None para o swipe: não é um toque pontual.
            click=(
                None
                if action.startswith("swipe_")
                else self.action_manager.target_frame(
                    action,
                    detection,
                )
            ),

            state=self.state,
            frame=self._frame,
            detections=self._detections,
            lag=self._lag,
            cycle_count=self.cycle_count,
        )

    # =====================================================
    # TEMPO CORRIDO
    # =====================================================

    def _mark_cycle(self, category):
        """
        Fecha o ciclo atual e começa outro.
        """

        agora = time.monotonic()

        duracao = agora - self.cycle_at

        self.cycle_count += 1

        logger.info(
            "Ciclo %d fechado por '%s' em %s%s",
            self.cycle_count,
            category,
            formata_duracao(duracao),
            (
                f" (anterior {formata_duracao(self.cycle_last)})"
                if self.cycle_last is not None
                else " (contado desde o start)"
            ),
        )

        self.cycle_last = duracao
        self.cycle_at = agora

    def cycle_stats(self):
        """
        (tempo_corrido, ultimo_ciclo, quantos) para o HUD.

        ultimo_ciclo é None até o primeiro build/plane — antes
        disso não há com o que comparar.
        """

        return (
            time.monotonic() - self.cycle_at,
            self.cycle_last,
            self.cycle_count,
        )

    def summary(self):
        """
        O que interessa a quem está olhando a tela, num dict.

        Nomes de negócio, não de código: quem acompanha o bot
        quer saber quantas vezes ele voou e reformou, não quantas
        vezes a categoria `fly` passou pelo `_act`.
        """

        return {
            "estado": self.state,
            "acao": self.last_action,
            "acao_ha": (
                None
                if self.last_action_at is None
                else time.monotonic() - self.last_action_at
            ),

            # `fly` é o botão do avião na tela de reforma; é ele
            # que efetivamente muda de restaurante.
            "voos": self.category_counts.get("fly", 0),

            # `build` e `plane` são as duas portas para RENOVATE
            # (CYCLE_CATEGORIES). Somadas, é quantas vezes o bot
            # entrou numa reforma; `cycle_count` conta o mesmo
            # evento, e serve de conferência.
            "reformas": sum(
                self.category_counts.get(categoria, 0)
                for categoria in CYCLE_CATEGORIES
            ),

            "acoes": sum(self.action_counts.values()),
        }

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

    def frame_floor(self):
        """
        O instante de CAPTURA que um frame precisa passar para
        poder autorizar uma ação. Frame anterior a isto é
        inútil para decidir — mostra a tela de antes do efeito
        da última ação.

        Uma definição só, em um lugar só: o `_can_act` usa para
        barrar a decisão, e o VisionWorker usa para não gastar
        uma passada inteira num frame que já se sabe que não vai
        autorizar nada. Se os dois calculassem por conta
        própria, um dia divergiriam — e o sintoma seria o bot
        agindo sobre tela velha de novo.

        DE QUANDO SE CONTA: do FIM da ação, não da submissão
        dela. As ações são assíncronas e algumas são longas — o
        swipe leva 500 ms, o long press de comida leva 4 s.
        Contando da submissão, o prazo já estava vencido quando
        a ação terminava, e o bot decidia sobre um frame
        capturado no MEIO dela.

        O max() cobre a janela em que a ação foi submetida mas
        ainda não terminou (aí vale a submissão) e o caso de um
        ActionManager que não reporte o fim (aí o comportamento
        é o antigo, nunca pior).

        QUANTO: swipe tem espera própria. Um toque mexe um
        painel; um swipe move a vista inteira e o jogo segue
        deslizando depois de o dedo sair.

        E, por cima de tudo: a espera de ENTRADA no estado
        (STATE_ENTRY_SETTLE), que não depende de ação nenhuma —
        uma tela que abre com animação não fica pronta só porque
        o toque que a abriu assentou. O piso é o MAIOR dos dois.
        """

        # =============================================
        # ESPERA DE ENTRADA NO ESTADO
        # =============================================
        #
        # Independente da última ação: uma tela que abriu com
        # animação não fica pronta só porque o toque assentou.
        #
        # Sem isto, a tela de upgrade era fechada na hora — no
        # meio da animação o "X" já casa e os botões de upgrade
        # ainda não, então a regra de fechar (que vem depois na
        # prioridade) era a única a encontrar alvo.
        # O `if` não é otimização: sem ele, um estado SEM espera
        # de entrada teria piso == state_entered, e o primeiro
        # frame de idade zero cairia no `<=` e seria barrado por
        # empate. Estado sem espera precisa de piso zero, não de
        # piso "agora".
        entrada = STATE_ENTRY_SETTLE.get(self.state, 0.0)

        piso = (
            self.state_entered + entrada
            if entrada
            else 0.0
        )

        if not self.last_action_time:
            return piso

        base = max(
            self.last_action_time,
            getattr(
                self.action_manager,
                "last_finished_at",
                0.0,
            ),
        )

        settle = (
            self.swipe_settle
            if self._last_was_swipe
            else self.action_settle
        )

        return max(piso, base + settle)

    def _can_act(self):

        # Ação em andamento (ex.: long press de 4 s) precisa
        # terminar antes da próxima.
        if self.action_manager.is_busy():
            return False

        agora = time.monotonic()

        if agora - self.last_action_time < self.action_cooldown:
            return False

        # =============================================
        # A TELA EM MÃO JÁ MOSTRA O EFEITO DA AÇÃO?
        # =============================================
        #
        # O cooldown sozinho não basta. Com o detector em
        # ~535 ms de atraso e o cooldown em 500 ms, no momento
        # em que o cooldown libera a detecção em mão veio de um
        # frame capturado ANTES da última ação — ela não pode
        # mostrar o efeito dela.
        #
        # O sintoma era duplo toque: fechava o "MAX" e tocava
        # de novo no mesmo ponto, o que REABRIA o painel. Vale
        # para toda ação, mas dói mais nas de fechar, onde o
        # ponto de dispensa é também o que abre.
        #
        # Não é ajuste de cooldown: por mais alto que ele
        # fosse, um detector mais lento voltaria a estourar a
        # margem. A condição certa é causal, não temporal.
        #
        # E não basta o frame ser POSTERIOR à ação: enquanto a
        # animação de fechar não termina, um frame posterior
        # ainda mostra o painel. Daí o action_settle.
        #
        # =============================================
        # DE QUANDO SE CONTA, E QUANTO
        # =============================================
        #
        # DE QUANDO: do FIM da ação, não da submissão dela. As
        # ações são assíncronas e algumas são longas — o swipe
        # leva 500 ms, o long press de comida leva 4 s. Contando
        # da submissão, o settle já estava vencido no instante em
        # que a ação terminava, e o bot decidia sobre um frame
        # capturado no MEIO dela.
        #
        # O max() cobre a janela em que a ação foi submetida mas
        # ainda não terminou (aí vale a submissão) e o caso de um
        # ActionManager que não reporte o fim (aí o
        # comportamento é o antigo, nunca pior).
        #
        # QUANTO: swipe tem espera própria. Um toque mexe um
        # painel; um swipe move a vista inteira e o jogo segue
        # deslizando depois de o dedo sair. Era exatamente o
        # buraco: o bot rolava a tela, detectava um alvo num
        # frame em que a vista ainda escorregava, e tocava onde
        # o alvo ESTAVA.
        # Piso 0 = nada a esperar. Antes a condição era "houve
        # ação alguma vez?", que ignorava a espera de ENTRADA no
        # estado — e é ela que impede o painel de upgrade de ser
        # fechado no meio da animação de abrir.
        piso = self.frame_floor()

        if (
            self._frame_time is not None
            and piso
            and self._frame_time <= piso
        ):

            if (
                logger.isEnabledFor(logging.DEBUG)
                and agora - self._waited_warned > 1.0
            ):

                logger.debug(
                    "%s: esperando frame que mostre o efeito "
                    "de %s (falta %.0f ms)",
                    self.state,
                    (
                        "um swipe"
                        if self._last_was_swipe
                        else "ação"
                    ),
                    (piso - self._frame_time) * 1000,
                )

                self._waited_warned = agora

            return False

        return True

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

        if state != FOOD:

            self._delay_exploration()

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

            if self._act("upgrade_food", up_food):

                self._delay_exploration()

                self.up_food_wait_start = time.monotonic()

            return

        # -------------------------------------------------
        # Painel de upgrade esgotado: dispensa.
        # -------------------------------------------------
        #
        # As duas dispensam o painel tocando num ponto fixo; o
        # ponto é que difere (DISMISS_POINT contra
        # GRAY_COIN_POINT).
        #
        # ANTES do _wait_or_give_up, e não depois: o
        # _wait_or_give_up pode chamar _enter(NORMAL), e um
        # _act(..., NORMAL) depois dele agiria já fora do estado
        # FOOD — decidindo por uma regra de FOOD numa tela que a
        # máquina já considera NORMAL.
        for categoria, acao in (
            ("gray_max", "gray_max"),
            ("gray_coin", "gray_coin"),
        ):

            deteccao = self._find(detections, categoria)

            if deteccao is not None:

                # self._delay_exploration()

                self._act(acao, deteccao, NORMAL)

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

            self._delay_exploration()

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

    def _delay_exploration(self):
        """
        Algo foi encontrado: ADIA o próximo swipe.

        Não se chama mais `_reset_exploration` porque resetar é
        exatamente o que ela não pode fazer. O ciclo de varredura
        (5 para um lado, 5 para o outro) segue de onde estava; o
        que muda é só quando o próximo swipe é permitido.

        Antes a contagem voltava a zero aqui, e o bot varria
        sempre o mesmo pedaço da tela: como quase todo swipe
        revela algum alvo, a volta nunca chegava ao fim.
        """

        if not SWIPE_WAIT_FOR_NO_ACTION:
            return

        self.explore_anchor = time.monotonic()

        self.explore_found = True

    def exploration_interval(self):
        """
        Quanto esperar pelo próximo swipe.

            não achou nada .... exploration_delay        (5 s)
            achou algo ........ exploration_action_delay (15 s)

        Derivado da bandeira em vez de guardado: ver
        explore_found.
        """

        return (
            self.exploration_action_delay
            if self.explore_found
            else self.exploration_delay
        )

    def _explore_screen(self):

        now = time.monotonic()

        if (
            now - self.explore_anchor
            < self.exploration_interval()
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

        # Volta ao RITMO normal: a partir daqui o próximo swipe
        # é em exploration_delay. Achar algo é o que troca isso
        # pelos 15 s (_delay_exploration).
        self.explore_anchor = now

        self.explore_found = False

        # A partir daqui vale SWIPE_WAITING_TIME, não
        # ACTION_SETTLE: a vista acabou de se mover inteira, e
        # nenhuma detecção de antes dela parar vale um toque.
        self._last_was_swipe = True

        # A exploração não passa por _act, então precisa gravar
        # aqui. É decisão do bot como qualquer outra: "não achei
        # nada, rolo a tela" — e para o treino subir e descer são
        # rótulos diferentes, daí a direção ir no NOME.
        self._record(
            f"swipe_{self.swipe_direction}",
            None,
        )

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

"""
Testes da máquina de estados.

    python tests/test_state_machine.py

Não precisa de device: o ActionManager é falso e o relógio
é controlado, então dá para testar cooldown, timeout de
estado e prioridade sem esperar em tempo real.
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "src"))

from core import log                              # noqa: E402
from core.config import (                         # noqa: E402
    ACTION_COOLDOWN,
    ACTION_SETTLE,
    DISMISS_ACTIONS,
    DISMISS_ATTEMPTS_BEFORE_SCROLL,
    MAX_DETECTION_AGE,
    REPEATED_ACTION_WARNING,
    STATE_ENTRY_SETTLE,
    STATE_TIMEOUTS,
    SWIPE_WAITING_TIME,
    UP_FOOD_WAIT,
)
from core import state_machine as sm              # noqa: E402


# =========================================================
# RELÓGIO CONTROLADO
# =========================================================

class Clock:

    def __init__(self):

        self.now = 10_000.0

    def monotonic(self):

        return self.now

    def advance(self, seconds):

        self.now += seconds


# =========================================================
# ACTION MANAGER FALSO
# =========================================================

class FakeActions:

    def __init__(self):

        self.calls = []
        self.busy = False

    def execute(self, action, detection):

        self.calls.append(
            (
                action,
                detection["category"] if detection else None,
            )
        )

        return True

    def swipe(self, direction):

        self.calls.append(("swipe", direction))

        return True

    def is_busy(self):

        return self.busy

    # Conveniência para os testes
    @property
    def actions(self):

        return [call[0] for call in self.calls]


# =========================================================
# HELPERS
# =========================================================

def detection(category, confidence=0.99, x=100, y=200):

    return {
        "category": category,
        "name": f"{category}.png",
        "confidence": confidence,
        "color_similarity": 0.95,
        "min_threshold": 0.9,
        "x": x,
        "y": y,
        "width": 80,
        "height": 80,
    }


def build():
    """
    Devolve (machine, actions, clock) com o cooldown já
    vencido, para o primeiro update poder agir.
    """

    clock = Clock()

    time.monotonic = clock.monotonic

    actions = FakeActions()

    machine = sm.StateMachine(actions)

    # Deixa o cooldown vencido.
    machine.last_action_time = clock.now - 100

    return machine, actions, clock


# =========================================================
# TESTES
# =========================================================

def test_prioridade_upgrade_vence_food():

    machine, actions, _ = build()

    machine.update([
        detection("food"),
        detection("upgrade"),
    ])

    assert actions.actions == ["upgrade"], actions.actions
    assert machine.state == sm.UPGRADE, machine.state


def test_fechar_vem_antes_do_jogo():
    """
    close/open_store/gray_max fecham o que não deveria estar
    aberto, então têm prioridade sobre plane e food.
    """

    machine, actions, _ = build()

    machine.update([
        detection("plane"),
        detection("close"),
    ])

    assert actions.actions == ["close"], actions.actions

    # Fechar uma aba não muda de estado.
    assert machine.state == sm.NORMAL, machine.state


def test_uma_acao_por_cooldown():

    machine, actions, clock = build()

    detections = [detection("box")]

    machine.update(detections)

    # Segundo frame imediatamente depois: cooldown barra.
    machine.update(detections)

    assert len(actions.calls) == 1, actions.calls

    clock.advance(ACTION_COOLDOWN + 0.01)

    machine.update(detections)

    assert len(actions.calls) == 2, actions.calls


def test_acao_em_andamento_bloqueia():
    """
    O long press de comida dura 4 s. Enquanto ele roda,
    nenhum outro clique pode ser enfileirado.
    """

    machine, actions, _ = build()

    actions.busy = True

    machine.update([detection("food")])

    assert actions.calls == [], actions.calls
    assert machine.state == sm.NORMAL, machine.state


def test_timeout_do_estado_volta_para_normal():
    """
    Antes, um modal sem template travava o bot para sempre:
    RENOVATE só saía achando a moeda.
    """

    machine, actions, clock = build()

    machine._enter(sm.RENOVATE)

    # Nada na tela: sem timeout, ficaria preso aqui.
    machine.update([])

    assert machine.state == sm.RENOVATE, machine.state

    clock.advance(STATE_TIMEOUTS["RENOVATE"] + 0.1)

    machine.update([])

    assert machine.state == sm.NORMAL, machine.state


def test_deteccao_velha_nao_clica():

    machine, actions, _ = build()

    machine.update(
        [detection("food")],
        lag=MAX_DETECTION_AGE + 0.1,
    )

    assert actions.calls == [], actions.calls


def test_upgrade_permanece_enquanto_houver_item():

    machine, actions, clock = build()

    machine._enter(sm.UPGRADE)

    # O painel abre com animação: STATE_ENTRY_SETTLE segura a
    # mão antes de agir aqui dentro. Ver
    # test_upgrade_nao_fecha_no_meio_da_animacao.
    clock.advance(STATE_ENTRY_SETTLE[sm.UPGRADE] + 0.01)

    machine.update([detection("up_upgrade")])

    assert actions.actions == ["upgrade_item"], actions.actions
    assert machine.state == sm.UPGRADE, machine.state

    # Sem mais itens: fecha e volta.
    clock.advance(ACTION_COOLDOWN + 0.01)

    machine.update([detection("close")])

    assert actions.actions[-1] == "close", actions.actions
    assert machine.state == sm.NORMAL, machine.state


def test_upgrade_nao_fecha_no_meio_da_animacao():
    """
    O bug relatado: ao abrir a tela de upgrade, o bot fechava
    ela na hora.

    As regras de UPGRADE são, em ordem, `up_upgrade` (evolui) e
    `close` (fecha e sai). Enquanto o painel entra na tela, o
    "X" já casa com o template e os botões de upgrade ainda não
    — então a regra 1 não acha nada, a regra 2 acha, e o bot
    desfaz o que acabou de fazer.

    ACTION_SETTLE não cobre isto: ele é contado do TOQUE que
    abriu e é dimensionado para animação de FECHAR painel.
    """

    machine, actions, clock = build()

    espera = STATE_ENTRY_SETTLE[sm.UPGRADE]

    assert espera > ACTION_SETTLE, (
        "uma espera de entrada menor que a de ação não teria "
        "efeito nenhum"
    )

    machine._enter(sm.UPGRADE)

    # -----------------------------------------------------
    # No meio da animação: o "X" é a única coisa que casa.
    # -----------------------------------------------------
    #
    # Já passado o ACTION_SETTLE, para provar que é a espera de
    # ENTRADA que está segurando — e não a de ação.

    clock.advance(ACTION_SETTLE + 0.01)

    machine.update([detection("close")])

    assert actions.actions == [], (
        "fechou o painel no meio da animação de abrir"
    )

    assert machine.state == sm.UPGRADE, machine.state

    # -----------------------------------------------------
    # Painel montado: agora o upgrade aparece e é ele que vence.
    # -----------------------------------------------------

    clock.advance(espera)

    machine.update([
        detection("up_upgrade"),
        detection("close"),
    ])

    assert actions.actions == ["upgrade_item"], actions.actions

    assert machine.state == sm.UPGRADE, machine.state


def test_espera_de_entrada_cabe_no_timeout_do_estado():
    """
    A espera de entrada come o tempo que o bot tem para agir
    dentro do estado: os dois vêm do mesmo relógio.
    """

    for estado, espera in STATE_ENTRY_SETTLE.items():

        limite = STATE_TIMEOUTS.get(estado)

        if limite is None:
            continue

        assert espera < limite / 2, (
            estado,
            espera,
            limite,
        )


def test_gray_coin_dispensa_como_o_gray_max():
    """
    `gray_coin` faz o que o `gray_max` faz — dispensa o painel e
    volta para NORMAL. O que muda é só o ponto tocado, e isso é
    assunto do ActionManager
    (test_pipeline::test_gray_coin_toca_no_ponto_proprio).
    """

    for categoria in ("gray_max", "gray_coin"):

        machine, actions, _ = build()

        machine.update([detection(categoria)])

        assert actions.actions == [categoria], (
            categoria,
            actions.actions,
        )

        # E em FOOD também: é lá que o painel esgotado aparece.
        machine, actions, _ = build()

        machine._enter(sm.FOOD)

        machine.update([detection(categoria)])

        assert actions.actions == [categoria], (
            categoria,
            actions.actions,
        )

        assert machine.state == sm.NORMAL, machine.state


def test_dispensa_em_food_acontece_antes_de_desistir():
    """
    Em FOOD, a dispensa tem de ser avaliada ANTES do
    _wait_or_give_up.

    O _wait_or_give_up pode chamar _enter(NORMAL). Se a dispensa
    vier depois dele, a ação sai quando a máquina JÁ SE CONSIDERA
    em NORMAL — decidindo por uma regra de FOOD num estado que
    não é mais FOOD, e rotulando a amostra do dataset com o
    estado errado.
    """

    machine, actions, clock = build()

    # Espia o estado NO MOMENTO em que a ação é despachada.
    estados = []

    original = actions.execute

    def espiao(action, deteccao):

        estados.append(machine.state)

        return original(action, deteccao)

    actions.execute = espiao

    machine._enter(sm.FOOD)

    # Timer de desistência JÁ VENCIDO: é a situação em que o
    # _wait_or_give_up agiria.
    machine.up_food_wait_start = clock.now

    clock.advance(UP_FOOD_WAIT + 0.1)

    machine.update([detection("gray_coin")])

    assert actions.actions == ["gray_coin"], actions.actions

    assert estados == [sm.FOOD], (
        f"dispensou já em {estados} — o _wait_or_give_up correu "
        f"antes e trocou o estado debaixo da ação"
    )


def test_food_desiste_depois_da_espera():

    machine, actions, clock = build()

    machine._enter(sm.FOOD)

    machine.update([detection("up_food")])

    assert actions.actions == ["upgrade_food"], actions.actions
    assert machine.state == sm.FOOD, machine.state

    # Nenhum up_food novo apareceu.
    clock.advance(UP_FOOD_WAIT + 0.1)

    machine.update([])

    assert machine.state == sm.NORMAL, machine.state


def test_exploracao_faz_swipe_e_inverte():

    machine, actions, clock = build()

    for _ in range(machine.max_swipes):

        clock.advance(machine.exploration_delay + 0.1)

        machine.update([])

    swipes = [
        call for call in actions.calls
        if call[0] == "swipe"
    ]

    assert len(swipes) == machine.max_swipes, swipes

    # Não fixa QUAL direção começa (é ajuste de config):
    # verifica que todas foram na mesma.
    primeira = swipes[0][1]

    assert all(
        call[1] == primeira for call in swipes
    ), swipes

    # Passou do limite: inverte.
    clock.advance(machine.exploration_delay + 0.1)

    machine.update([])

    oposta = "up" if primeira == "down" else "down"

    assert actions.calls[-1] == ("swipe", oposta), (
        actions.calls[-1],
        oposta,
    )


def test_deteccao_reseta_exploracao():

    machine, actions, clock = build()

    clock.advance(machine.exploration_delay + 0.1)

    machine.update([detection("box")])

    # Achou algo: não está perdido, não deve fazer swipe.
    assert ("swipe", "up") not in actions.calls, actions.calls


def test_categorias_do_estado_sao_reduzidas():
    """
    É o que faz a passada do detector cair de ~264 ms para
    ~16 ms dentro da tela de upgrade.
    """

    machine, _, _ = build()

    normal = machine.wanted_categories()

    machine._enter(sm.UPGRADE)

    upgrade = machine.wanted_categories()

    assert "food" in normal
    assert "food" not in upgrade, upgrade
    assert list(upgrade) == ["up_upgrade", "close"], upgrade


def test_categorias_vem_na_ordem_da_prioridade():
    """
    A ORDEM é contrato, não detalhe: o detector para de
    procurar na primeira categoria que encontrar, então uma
    ordem embaralhada faria o bot agir na regra errada — e
    `food`, que é 2/3 dos templates, deixaria de ser a última.
    """

    machine, _, _ = build()

    categorias = list(machine.wanted_categories())

    esperada = [
        category
        for category, _, _ in sm.NORMAL_RULES
    ]

    assert categorias == esperada, categorias

    # Sem repetição: categoria repetida seria template
    # procurado duas vezes na mesma passada.
    assert len(categorias) == len(set(categorias)), categorias

    # E `food` continua no fim, que é onde a economia vive.
    assert categorias[-1] == "food", categorias


def test_estado_nao_vaza_variavel():
    """
    up_food_wait_start era compartilhada entre FOOD e
    NEW_POINT.
    """

    machine, _, clock = build()

    machine._enter(sm.FOOD)

    machine.update([detection("up_food")])

    assert machine.up_food_wait_start is not None

    machine._enter(sm.NEW_POINT)

    assert machine.up_food_wait_start is None


def test_up_food_em_normal_evolui_a_comida():
    """
    up_food em NORMAL faz o long press de evolução, igual ao
    estado FOOD.

    ESCOLHA DELIBERADA do dono do projeto. Não é o que parece
    "seguro": este caminho GASTA MOEDA a cada painel de comida
    que abre sem querer, e trava o bot pelos
    UPGRADE_FOOD_PRESS segundos do press.

    A alternativa era dispensar num ponto neutro. Se algum dia
    quiser voltar, é trocar a ação para "dismiss" em
    NORMAL_RULES — a ação continua implementada e testada.
    """

    machine, actions, _ = build()

    machine.update([detection("up_food")])

    assert actions.actions == ["upgrade_food"], actions.actions

    # A regra não declara próximo estado: segue em NORMAL.
    assert machine.state == sm.NORMAL, machine.state


def test_up_food_faz_o_mesmo_em_normal_e_em_food():
    """
    A mesma ação nos dois estados, por escolha do dono. O que
    muda é só o estado em que fica.

    Este teste existe para o dia em que alguém "corrigir" um
    dos dois lados sem olhar o outro.
    """

    normal, acoes_normal, _ = build()

    normal.update([detection("up_food")])

    assert acoes_normal.actions == ["upgrade_food"]

    machine, actions, _ = build()

    machine._enter(sm.FOOD)

    machine.update([detection("up_food")])

    assert actions.actions == ["upgrade_food"], actions.actions


def test_up_food_perde_para_o_close():
    """
    O X fecha o painel sem gastar nada, então vem antes do
    long press de evolução.
    """

    machine, actions, _ = build()

    machine.update([
        detection("up_food"),
        detection("close"),
    ])

    assert actions.actions == ["close"], actions.actions


def test_up_food_vence_acao_de_jogo():
    """
    Fechar o que não deveria estar aberto vem antes de
    qualquer ação de jogo.
    """

    machine, actions, _ = build()

    machine.update([
        detection("food"),
        detection("up_food"),
        detection("box"),
    ])

    assert actions.actions == ["upgrade_food"], actions.actions


def test_acao_repetida_gera_aviso():
    """
    NORMAL não tem timeout: uma regra que dispara sem
    resolver repetiria para sempre em silêncio.
    """

    import logging

    machine, actions, clock = build()

    avisos = []

    class Coletor(logging.Handler):

        def emit(self, record):

            if record.levelno >= logging.WARNING:

                avisos.append(record.getMessage())

    handler = Coletor()

    sm.logger.addHandler(handler)

    # O runner roda em ERROR; sem baixar o nível aqui, o
    # warning é descartado antes de chegar ao handler.
    nivel_original = sm.logger.level

    sm.logger.setLevel(logging.WARNING)

    try:

        # O painel nunca fecha: up_food continua na tela.
        for _ in range(REPEATED_ACTION_WARNING):

            machine.update([detection("up_food")])

            clock.advance(ACTION_COOLDOWN + 0.01)

    finally:

        sm.logger.removeHandler(handler)

        sm.logger.setLevel(nivel_original)

    assert len(actions.calls) == REPEATED_ACTION_WARNING, (
        actions.calls
    )

    assert avisos, "nenhum aviso de ação repetida"

    assert "upgrade_food" in avisos[0], avisos


def test_contagem_de_repeticao_zera_ao_progredir():

    machine, actions, clock = build()

    machine.update([detection("up_food")])

    assert machine._repeat_count == 1

    clock.advance(ACTION_COOLDOWN + 0.01)

    # Ação diferente: a contagem reinicia.
    machine.update([detection("box")])

    assert machine._repeat_count == 1, machine._repeat_count

    assert machine._last_action == "open_box", (
        machine._last_action
    )


def test_max_preso_rola_a_tela_e_tenta_de_novo():
    """
    O cenário que motivou isso: o ponto fixo ABRE um painel, o
    painel mostra "max", a regra do gray_max toca o mesmo ponto,
    e reabre.

    A saída não é o BACK (neste jogo ele SAI DO JOGO): é rolar a
    tela até o fim, onde o canto de baixo fica vazio, e tentar o
    ponto de novo.
    """

    machine, actions, clock = build()

    frames = (DISMISS_ATTEMPTS_BEFORE_SCROLL + 1) * 2

    for _ in range(frames):

        machine.update([detection("gray_max")])

        clock.advance(ACTION_COOLDOWN + 0.01)

    esperado = (
        ["gray_max"] * DISMISS_ATTEMPTS_BEFORE_SCROLL
        + ["scroll_bottom"]
    ) * 2

    assert actions.actions == esperado, actions.actions

    # E não para no primeiro escape: o ciclo se repete.
    assert actions.actions.count("scroll_bottom") == 2, (
        actions.actions
    )


def test_nunca_usa_back():
    """
    BACK sai do jogo neste jogo. Se alguém reintroduzir, este
    teste avisa antes de o bot fechar o jogo sozinho.
    """

    from actions.manager import ACTION_TABLE

    assert "back" not in ACTION_TABLE, (
        "BACK sai do EatVenture — não pode entrar na tabela"
    )

    machine, actions, clock = build()

    for _ in range(12):

        machine.update([detection("gray_max")])

        clock.advance(ACTION_COOLDOWN + 0.01)

    assert "back" not in actions.actions, actions.actions


def test_up_food_em_normal_nao_escala_para_rolagem():
    """
    A escada de fechamento vale só para DISMISS_ACTIONS, e
    "upgrade_food" não é uma delas: é ação de jogo.

    Ou seja, up_food preso em NORMAL repete o press para
    sempre em vez de rolar a tela. É consequência direta da
    escolha de usar upgrade_food ali — o aviso de ação
    repetida (test_acao_repetida_gera_aviso) é o que sobra
    para avisar.
    """

    machine, actions, clock = build()

    for _ in range(DISMISS_ATTEMPTS_BEFORE_SCROLL + 3):

        machine.update([detection("up_food")])

        clock.advance(ACTION_COOLDOWN + 0.01)

    assert "scroll_bottom" not in actions.actions, actions.actions

    assert set(actions.actions) == {"upgrade_food"}, (
        actions.actions
    )

    # A escada nem começou a contar.
    assert machine._dismiss_attempts == 0


# =========================================================
# DUPLO TOQUE
# =========================================================

def test_nao_age_duas_vezes_sobre_a_mesma_tela():
    """
    O bug relatado: fechava o "MAX" e tocava DE NOVO no mesmo
    ponto, o que REABRIA o painel.

    Causa: o cooldown (0.5 s) libera antes de existir frame que
    mostre o efeito da ação, porque o detector está com ~0.535 s
    de atraso. A detecção em mão veio de ANTES do toque.

    Reproduzido com os números medidos no device.
    """

    COOLDOWN = 0.5
    ATRASO = 0.535

    machine, actions, clock = build()

    machine.action_cooldown = COOLDOWN

    # Primeiro toque, sobre uma tela recém-vista.
    machine.update([detection("gray_max")], 0.0)

    assert actions.actions == ["gray_max"], actions.actions

    # Passa o cooldown. A detecção continua sendo a MESMA tela
    # de antes do toque — é o que o atraso do detector entrega.
    clock.advance(COOLDOWN + 0.01)

    machine.update([detection("gray_max")], ATRASO)

    assert actions.actions == ["gray_max"], (
        "tocou duas vezes sobre a mesma tela: "
        f"{actions.actions}"
    )


def test_age_quando_o_frame_e_posterior_a_acao():
    """
    O outro lado: chegando frame de DEPOIS da ação, e o painel
    ainda estando lá, tem de agir — senão a correção acima
    viraria paralisia.
    """

    machine, actions, clock = build()

    machine.action_cooldown = 0.5

    machine.update([detection("gray_max")], 0.0)

    clock.advance(0.6)

    # Atraso pequeno: o frame é de depois do toque.
    machine.update([detection("gray_max")], 0.05)

    assert actions.actions == ["gray_max", "gray_max"], (
        actions.actions
    )


def test_frame_antigo_nao_bloqueia_para_sempre():
    """
    Com atraso alto, o bot espera — mas volta a agir assim que
    chega frame novo. O risco da correção seria travar o bot
    quando o detector está lento.
    """

    machine, actions, clock = build()

    machine.action_cooldown = 0.5

    machine.update([detection("gray_max")], 0.0)

    # Vários frames velhos: nenhum age.
    for _ in range(5):

        clock.advance(0.6)

        machine.update([detection("gray_max")], 10.0)

    assert actions.actions == ["gray_max"], actions.actions

    # Frame fresco: age.
    machine.update([detection("gray_max")], 0.01)

    assert len(actions.actions) == 2, actions.actions


def test_primeira_acao_nao_precisa_esperar():
    """
    Sem ação anterior não há o que aguardar. Sem esta guarda o
    bot não faria NADA no start, porque last_action_time = 0
    é anterior a qualquer frame.
    """

    machine, actions, _ = build()

    machine.last_action_time = 0.0

    # Abaixo de MAX_DETECTION_AGE: acima dela cairia na guarda
    # de detecção velha, que é outra coisa.
    machine.update([detection("gray_max")], 1.0)

    assert actions.actions == ["gray_max"], actions.actions


def test_a_guarda_vale_para_todo_caminho_de_acao():
    """
    A checagem vive em _can_act, que é por onde passam TODOS
    os caminhos: regras, handlers de FOOD/NEW_POINT e o swipe
    de exploração. Testada aqui direto, no ponto único.

    (Não dá para montar o caso pela exploração: ela exige
    exploration_delay = 5 s de tela vazia, e um frame anterior
    à ação nessa janela já teria sido descartado por
    MAX_DETECTION_AGE = 2 s antes de chegar aqui.)
    """

    machine, actions, clock = build()

    machine.action_cooldown = 0.0
    machine.action_settle = 0.4

    machine.last_action_time = clock.now

    # Frame capturado ANTES da ação: barrado.
    machine._frame_time = clock.now - 0.1

    assert not machine._can_act()

    # Frame do MESMO instante: barrado. Não pode mostrar o
    # efeito de algo que acabou de acontecer.
    machine._frame_time = clock.now

    assert not machine._can_act()

    # Frame posterior à ação MAS dentro da animação: barrado.
    # É o caso que a guarda causal sozinha deixava passar, e
    # que causava o duplo toque no "MAX".
    machine._frame_time = clock.now + 0.2

    assert not machine._can_act(), (
        "frame dentro da janela de animação não pode agir"
    )

    # Passado o settle: liberado.
    machine._frame_time = clock.now + 0.41

    assert machine._can_act()


def test_swipe_espera_a_vista_parar():
    """
    O bug relatado: depois de rolar a tela, o bot detectava um
    alvo num frame capturado enquanto a vista ainda escorregava
    e tocava onde o alvo ESTAVA.

    Um swipe não é um toque: ele move a VISTA INTEIRA, e o jogo
    continua deslizando por inércia depois de o dedo sair. Daí a
    espera própria (SWIPE_WAITING_TIME), contada do FIM do
    gesto.
    """

    machine, actions, clock = build()

    # Tela vazia agora mesmo: dispara a exploração.
    machine.exploration_delay = 0.0

    machine.update([], lag=0.0)

    assert ("swipe", "down") in actions.calls, actions.calls

    momento_do_swipe = clock.now

    # -----------------------------------------------------
    # O swipe LEVA TEMPO para executar (SWIPE_DURATION_MS).
    # -----------------------------------------------------
    #
    # É o ponto do bug: medida da submissão, a espera seria
    # consumida pelo próprio gesto e não sobraria nada.

    clock.advance(0.5)

    actions.last_finished_at = clock.now

    fim_do_swipe = clock.now

    # -----------------------------------------------------
    # Alvo visível, mas a vista ainda está escorregando.
    # -----------------------------------------------------
    #
    # PASSADA a espera de um toque, e ainda assim barrado: é aí
    # que a espera do swipe se distingue da de toque. Testar num
    # instante qualquer antes de ACTION_SETTLE não provaria nada
    # — a espera curta já barraria sozinha.

    clock.advance(ACTION_SETTLE + 0.01)

    machine.update([detection("food")], lag=0.0)

    assert machine.action_counts["food"] == 0, (
        "tocou antes de a vista parar — é o clique que cai no "
        "lugar errado"
    )

    # -----------------------------------------------------
    # Passado SWIPE_WAITING_TIME do FIM do swipe: liberado.
    # -----------------------------------------------------

    clock.advance(SWIPE_WAITING_TIME)

    assert clock.now > fim_do_swipe + SWIPE_WAITING_TIME

    machine.update([detection("food")], lag=0.0)

    assert machine.action_counts["food"] == 1, (
        machine.action_counts
    )

    # E a espera foi contada do fim do gesto, não da submissão:
    # senão este teste passaria com o bug dentro.
    assert (
        clock.now - momento_do_swipe
        > SWIPE_WAITING_TIME + 0.5
    )


def test_piso_do_frame_concorda_com_o_can_act():
    """
    `frame_floor()` é o MESMO prazo que o `_can_act` aplica —
    ele existe para o VisionWorker poder pular frame que a
    máquina descartaria de todo jeito.

    Se os dois divergirem, o worker joga fora frame que serviria
    (bot cego) ou analisa frame que não serve (passada perdida).
    Este teste é a amarra entre eles.
    """

    # Máquina recém-nascida (o build() adianta o relógio da
    # última ação para vencer o cooldown, então não serve para
    # este caso): sem ação nenhuma, não há o que descartar, e um
    # piso qualquer aqui cegaria o bot no arranque.
    assert sm.StateMachine(FakeActions()).frame_floor() == 0.0

    machine, actions, clock = build()

    machine.action_cooldown = 0.0

    assert machine._act("food", detection("food")) is True

    actions.last_finished_at = clock.now

    piso = machine.frame_floor()

    assert piso == clock.now + ACTION_SETTLE, piso

    # Exatamente no piso: barrado pelos dois.
    machine._frame_time = piso

    assert not machine._can_act()

    # Um fio acima: liberado pelos dois.
    machine._frame_time = piso + 0.001

    assert machine._can_act()

    # E o piso do swipe é o maior dos dois.
    machine._last_was_swipe = True

    assert machine.frame_floor() == clock.now + SWIPE_WAITING_TIME


def test_swipe_espera_mais_que_um_toque():
    """
    SWIPE_WAITING_TIME abaixo de ACTION_SETTLE não teria
    sentido: o swipe mexe MAIS na tela que um toque, então
    esperar menos por ele seria o contrário do que se quer.
    """

    assert SWIPE_WAITING_TIME >= ACTION_SETTLE, (
        SWIPE_WAITING_TIME,
        ACTION_SETTLE,
    )

    # E não tanto que a exploração fique lenta: o bot faz
    # MAX_SWIPES seguidos procurando conteúdo.
    assert SWIPE_WAITING_TIME <= 2.0, SWIPE_WAITING_TIME


def test_scroll_bottom_usa_a_espera_do_swipe():
    """
    `scroll_bottom` passa pelo caminho das ações normais, mas é
    SEIS swipes seguidos — mexe a vista mais que qualquer swipe
    solto. Se ele usasse a espera curta, a escada de escape
    voltaria a tocar sobre tela em movimento.
    """

    machine, actions, clock = build()

    machine.action_cooldown = 0.0

    assert machine._act("scroll_bottom", None) is True

    assert machine._last_was_swipe is True, (
        "scroll_bottom tem de contar como movimento de vista"
    )

    actions.last_finished_at = clock.now

    # Dentro da espera de swipe (mas fora da de toque): barrado.
    machine._frame_time = clock.now + ACTION_SETTLE + 0.01

    assert not machine._can_act(), (
        "a espera curta de toque não serve para seis swipes"
    )

    machine._frame_time = clock.now + SWIPE_WAITING_TIME + 0.01

    assert machine._can_act()


def test_toque_comum_nao_herda_a_espera_do_swipe():
    """
    A bandeira tem de VOLTAR: um swipe seguido de toques não
    pode deixar todos os toques seguintes pagando a espera
    longa.
    """

    machine, actions, clock = build()

    machine.action_cooldown = 0.0

    machine._last_was_swipe = True

    assert machine._act("food", detection("food")) is True

    assert machine._last_was_swipe is False

    actions.last_finished_at = clock.now

    # Basta a espera de toque.
    machine._frame_time = clock.now + ACTION_SETTLE + 0.01

    assert machine._can_act()


def test_nao_toca_com_o_painel_ja_fechado():
    """
    O bug relatado, reproduzido no domínio do tempo.

    Simula a VERDADE do jogo e a visão ATRASADA da máquina:

      - o painel está aberto
      - toque com painel aberto  -> fecha (após a animação)
      - toque com painel fechado -> ABRE (o DISMISS_POINT abre
        algo; é justamente por isso que o duplo toque dói)
      - a máquina só vê a tela de `atraso` segundos atrás

    O toque espúrio é o dado quando o painel JÁ estava
    fechado. Sem a guarda saem vários; com ela, nenhum.
    """

    ATRASO = 0.535     # medido no device, 165 templates
    COOLDOWN = 0.5
    ANIMACAO = 0.3     # tempo do jogo para fechar

    def roda(settle):

        clock = Clock()

        sm.time.monotonic = clock.monotonic

        actions = FakeActions()

        machine = sm.StateMachine(actions)

        machine.action_cooldown = COOLDOWN
        machine.action_settle = settle
        machine.last_action_time = clock.now - 100

        # (instante em que passa a valer, aberto?)
        eventos = []

        def aberto(instante):

            for t0, valor in reversed(eventos):

                if instante >= t0:
                    return valor

            return True

        toques = espurios = 0

        fim = clock.now + 8.0

        while clock.now < fim:

            visto = aberto(clock.now - ATRASO)

            antes = actions.actions.count("gray_max")

            machine.update(
                [detection("gray_max")] if visto else [],
                ATRASO,
            )

            if actions.actions.count("gray_max") > antes:

                toques += 1

                estava = aberto(clock.now)

                if not estava:
                    espurios += 1

                eventos.append((clock.now + ANIMACAO, not estava))

            clock.advance(0.02)

        return toques, espurios

    # Sem settle (só cooldown + guarda causal): toca no vazio.
    _, espurios_sem = roda(0.0)

    assert espurios_sem > 0, (
        "a simulação deveria reproduzir o bug com settle=0"
    )

    # Com settle cobrindo a animação: um toque, nenhum espúrio.
    toques, espurios = roda(ACTION_SETTLE)

    assert espurios == 0, f"{espurios} toque(s) no painel fechado"

    assert toques == 1, f"{toques} toques onde 1 bastava"


def test_settle_cobre_a_animacao_configurada():
    """
    A relação que faz a correção funcionar: o settle tem de ser
    maior que a animação do jogo. Se alguém baixar ACTION_SETTLE
    para 0.1, o duplo toque volta — e este teste diz por quê.
    """

    # Animação típica de painel de jogo.
    assert ACTION_SETTLE >= 0.3, ACTION_SETTLE

    # E não tão alto que o bot fique lento: acima de ~1s cada
    # decisão custaria settle + atraso do detector.
    assert ACTION_SETTLE <= 1.0, ACTION_SETTLE


def test_escada_zera_quando_algo_e_resolvido():
    """
    Se uma ação normal aconteceu, o bot saiu do buraco: a
    contagem recomeça, então não rola a tela por causa de
    tentativas antigas.
    """

    machine, actions, clock = build()

    for _ in range(DISMISS_ATTEMPTS_BEFORE_SCROLL):

        machine.update([detection("gray_max")])

        clock.advance(ACTION_COOLDOWN + 0.01)

    assert machine._dismiss_attempts == (
        DISMISS_ATTEMPTS_BEFORE_SCROLL
    )

    # Progresso: uma ação de jogo.
    machine.update([detection("box")])

    clock.advance(ACTION_COOLDOWN + 0.01)

    assert machine._dismiss_attempts == 0

    # Próximo gray_max começa do ponto, não da rolagem.
    machine.update([detection("gray_max")])

    assert actions.actions[-1] == "gray_max", actions.actions


def test_escada_zera_ao_trocar_de_estado():

    machine, actions, clock = build()

    machine.update([detection("gray_max")])

    assert machine._dismiss_attempts == 1

    machine._enter(sm.UPGRADE)

    assert machine._dismiss_attempts == 0
    assert machine._dismiss_rounds == 0


def test_apenas_acoes_de_fechar_escalam():
    """
    Ação de jogo não pode virar rolagem de tela por repetir.
    """

    machine, actions, clock = build()

    for _ in range(DISMISS_ATTEMPTS_BEFORE_SCROLL + 3):

        machine.update([detection("box")])

        clock.advance(ACTION_COOLDOWN + 0.01)

    assert set(actions.actions) == {"open_box"}, actions.actions

    assert "gray_max" in DISMISS_ACTIONS
    assert "dismiss" in DISMISS_ACTIONS
    assert "open_box" not in DISMISS_ACTIONS


# =========================================================
# RUNNER
# =========================================================

def main():

    log.setup("ERROR")

    real_monotonic = time.monotonic

    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]

    failures = 0

    for test in tests:

        try:

            test()

            print(f"  ok    {test.__name__}")

        except AssertionError as error:

            failures += 1

            print(f"  FALHA {test.__name__}: {error}")

        except Exception as error:

            failures += 1

            print(
                f"  ERRO  {test.__name__}: "
                f"{type(error).__name__}: {error}"
            )

        finally:

            time.monotonic = real_monotonic

    print()

    if failures:

        print(f"{failures}/{len(tests)} falharam.")

        return 1

    print(f"{len(tests)}/{len(tests)} passaram.")

    return 0


if __name__ == "__main__":

    sys.exit(main())

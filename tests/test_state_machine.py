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
    DISMISS_ACTIONS,
    DISMISS_ATTEMPTS_BEFORE_SCROLL,
    MAX_DETECTION_AGE,
    REPEATED_ACTION_WARNING,
    STATE_TIMEOUTS,
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

    machine.update([detection("up_upgrade")])

    assert actions.actions == ["upgrade_item"], actions.actions
    assert machine.state == sm.UPGRADE, machine.state

    # Sem mais itens: fecha e volta.
    clock.advance(ACTION_COOLDOWN + 0.01)

    machine.update([detection("close")])

    assert actions.actions[-1] == "close", actions.actions
    assert machine.state == sm.NORMAL, machine.state


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
    É o que faz a passada do detector cair de ~320 ms para
    ~20 ms dentro da tela de upgrade.
    """

    machine, _, _ = build()

    normal = machine.wanted_categories()

    machine._enter(sm.UPGRADE)

    upgrade = machine.wanted_categories()

    assert "food" in normal
    assert "food" not in upgrade, upgrade
    assert upgrade == {"up_upgrade", "close"}, upgrade


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


def test_up_food_em_normal_dispensa():
    """
    up_food visível em NORMAL = painel de comida abriu sem
    querer. Não há o que evoluir aqui, então dispensa.
    """

    machine, actions, _ = build()

    machine.update([detection("up_food")])

    assert actions.actions == ["dismiss"], actions.actions

    # Dispensar não muda de estado.
    assert machine.state == sm.NORMAL, machine.state


def test_mesmo_up_food_faz_o_oposto_em_food():
    """
    A MESMA categoria tem significado oposto conforme o
    estado: em NORMAL dispensa, em FOOD faz o long press.
    """

    machine, actions, _ = build()

    machine._enter(sm.FOOD)

    machine.update([detection("up_food")])

    assert actions.actions == ["upgrade_food"], actions.actions


def test_up_food_perde_para_o_close():
    """
    Se o X está na tela, fechar por ele é melhor que tocar
    num ponto neutro.
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

    assert actions.actions == ["dismiss"], actions.actions


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

    assert "dismiss" in avisos[0], avisos


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


def test_up_food_preso_tambem_rola():

    machine, actions, clock = build()

    for _ in range(DISMISS_ATTEMPTS_BEFORE_SCROLL + 1):

        machine.update([detection("up_food")])

        clock.advance(ACTION_COOLDOWN + 0.01)

    assert actions.actions == (
        ["dismiss"] * DISMISS_ATTEMPTS_BEFORE_SCROLL
        + ["scroll_bottom"]
    ), actions.actions


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

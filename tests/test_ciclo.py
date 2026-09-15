"""
Testes do tempo corrido entre reformas: o único número do HUD que mede
PROGRESSO. Contar ciclo errado faz o painel mentir sobre a coisa que
ele existe para mostrar.
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "src"))

from core import log                             # noqa: E402
from core import state_machine as sm             # noqa: E402
from core.metrics import formata_duracao         # noqa: E402

log.setup("ERROR")


class AcoesFalsas:
    """`livre` desliga o aceite, para imitar worker ocupado."""

    def __init__(self):

        self.executadas = []
        self.livre = True

    def execute(self, action, detection=None):

        if not self.livre:
            return False

        self.executadas.append(action)

        return True

    def is_busy(self):

        return not self.livre

    def set_frame_size(self, *a):
        pass


def deteccao(category, confidence=0.99):

    return {
        "category": category,
        "name": "item_001.png",
        "confidence": confidence,
        "color_similarity": 1.0,
        "x": 100,
        "y": 200,
        "width": 50,
        "height": 50,
    }


def maquina():

    acoes = AcoesFalsas()

    machine = sm.StateMachine(acoes)

    # Sem cooldown os testes não precisam dormir.
    machine.action_cooldown = 0.0

    # A guarda de "só age sobre frame que mostre o efeito da ação" tem
    # testes próprios em test_state_machine.py; aqui o assunto é a
    # CONTAGEM de ciclos.
    machine.action_settle = 0.0

    return machine, acoes


def agir(machine, categoria):
    """Um passo com essa detecção na tela, voltando a NORMAL."""

    machine.state = sm.NORMAL

    machine.update([deteccao(categoria)], 0.0)


def test_comeca_em_zero_contando_do_start():
    """Sem ciclo anterior o tempo corrido já vale: "8 min e não passou de restaurante" é informação."""

    machine, _ = maquina()

    corrido, ultimo, quantos = machine.cycle_stats()

    assert quantos == 0, quantos
    assert ultimo is None, ultimo
    assert 0.0 <= corrido < 1.0, corrido


def test_build_fecha_ciclo():

    machine, acoes = maquina()

    agir(machine, "build")

    corrido, ultimo, quantos = machine.cycle_stats()

    assert quantos == 1, quantos

    # O ciclo anterior existe, e o corrido reiniciou.
    assert ultimo is not None
    assert corrido < 0.5, corrido

    assert "click" in acoes.executadas, acoes.executadas


def test_plane_tambem_fecha_ciclo():
    """São as duas portas para RENOVATE: qualquer uma conta."""

    machine, _ = maquina()

    agir(machine, "plane")

    assert machine.cycle_stats()[2] == 1


def test_conta_acumulado():

    machine, _ = maquina()

    for categoria in ("build", "plane", "build"):
        agir(machine, categoria)

    assert machine.cycle_stats()[2] == 3


def test_ultimo_ciclo_mede_o_intervalo():

    machine, _ = maquina()

    agir(machine, "build")

    time.sleep(0.25)

    agir(machine, "build")

    _, ultimo, _ = machine.cycle_stats()

    assert 0.2 < ultimo < 0.6, ultimo


def test_upgrade_nao_fecha_ciclo():
    """O contador é de REFORMA, não de ação: clicar em upgrade não é progresso."""

    machine, _ = maquina()

    for categoria in ("upgrade", "new_point", "box", "food"):
        agir(machine, categoria)

    assert machine.cycle_stats()[2] == 0, machine.cycle_stats()


def test_acao_barrada_por_cooldown_nao_conta():
    """`_apply_rules` acha o build, mas o cooldown barra: nenhum toque, nenhum ciclo."""

    machine, acoes = maquina()

    machine.action_cooldown = 60.0

    # Consome o cooldown com uma ação qualquer.
    agir(machine, "upgrade")

    antes = machine.cycle_stats()[2]

    for _ in range(5):
        agir(machine, "build")

    assert machine.cycle_stats()[2] == antes, machine.cycle_stats()


def test_worker_ocupado_nao_conta():
    """Mesmo caso pelo outro caminho: a ação foi tentada e o ActionManager recusou."""

    machine, acoes = maquina()

    acoes.livre = False

    for _ in range(5):
        agir(machine, "build")

    assert machine.cycle_stats()[2] == 0, machine.cycle_stats()

    # E quando libera, conta uma vez.
    acoes.livre = True

    agir(machine, "build")

    assert machine.cycle_stats()[2] == 1


def test_corrido_cresce_com_o_tempo():

    machine, _ = maquina()

    primeiro = machine.cycle_stats()[0]

    time.sleep(0.2)

    segundo = machine.cycle_stats()[0]

    assert segundo > primeiro, (primeiro, segundo)


def test_formata_duracao():

    casos = [
        (None, "--"),
        (0, "0s"),
        (7.9, "7s"),
        (59, "59s"),
        (60, "1m00s"),
        (127, "2m07s"),
        (3599, "59m59s"),
        (3600, "1h00m"),
        (3725, "1h02m"),
    ]

    for entrada, esperado in casos:

        assert formata_duracao(entrada) == esperado, (
            entrada,
            formata_duracao(entrada),
        )


def test_formata_duracao_nao_estoura_com_negativo():
    """Um None ou negativo vindo de cálculo errado não pode virar exceção no desenho do HUD."""

    assert formata_duracao(-5) == "0s"


def test_hud_desenha_o_ciclo():

    import numpy as np
    from vision.detector import Detector

    detector = Detector()

    frame = np.zeros((2400, 1080, 3), np.uint8)

    limpo = detector.draw(frame.copy(), [], None)

    com_ciclo = detector.draw(
        frame.copy(),
        [],
        {"cycle": (127.0, 200.0, 3)},
    )

    # Sem stats nada é escrito; com stats, pixels mudaram.
    assert not np.array_equal(limpo, com_ciclo)


def test_hud_marca_travado():
    """
    Vermelho quando o corrido passa de CYCLE_STALL_FACTOR vezes o ciclo
    anterior. NUNCA vermelho sem ciclo anterior — sem referência,
    "demorado" não quer dizer nada.
    """

    import numpy as np
    from core.config import CYCLE_STALL_FACTOR
    from vision.detector import Detector

    detector = Detector()

    frame = np.zeros((2400, 1080, 3), np.uint8)

    def pinta(cycle):

        saida = detector.draw(frame.copy(), [], {"cycle": cycle})

        # O HUD usa contorno preto + cor. Conta pixels de cada.
        pixels = saida.reshape(-1, 3)

        return {
            "ok": int(
                np.all(pixels == detector.HUD_OK, axis=1).sum()
            ),
            "bad": int(
                np.all(pixels == detector.HUD_BAD, axis=1).sum()
            ),
            "neutro": int(
                np.all(
                    pixels == detector.HUD_NEUTRAL,
                    axis=1,
                ).sum()
            ),
        }

    ultimo = 100.0

    dentro = pinta((ultimo * (CYCLE_STALL_FACTOR - 0.5), ultimo, 2))
    fora = pinta((ultimo * (CYCLE_STALL_FACTOR + 0.5), ultimo, 2))

    assert dentro["bad"] == 0, dentro
    assert fora["bad"] > 0, fora

    # Sem ciclo anterior, nunca vermelho, mesmo com muito tempo.
    sem_referencia = pinta((99999.0, None, 0))

    assert sem_referencia["bad"] == 0, sem_referencia


def main():

    testes = [
        valor
        for nome, valor in sorted(globals().items())
        if nome.startswith("test_") and callable(valor)
    ]

    falhas = 0

    for teste in testes:

        try:

            teste()

            print(f"  ok    {teste.__name__}")

        except AssertionError as erro:

            falhas += 1

            print(f"  FALHA {teste.__name__}: {erro}")

        except Exception as erro:

            falhas += 1

            print(
                f"  ERRO  {teste.__name__}: "
                f"{type(erro).__name__}: {erro}"
            )

    print()

    if falhas:

        print(f"{falhas}/{len(testes)} falharam.")

        return 1

    print(f"{len(testes)}/{len(testes)} passaram.")

    return 0


if __name__ == "__main__":

    sys.exit(main())

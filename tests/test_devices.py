"""
Testes da escolha de device.

    python tests/test_devices.py

Nada aqui fala com adb: as fichas dos devices são injetadas.
O que está sob teste é a DECISÃO — qual serial sai — porque é
ela que, errada, faz o bot pilotar o aparelho errado.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "src"))

from core import devices                         # noqa: E402


# O caso real: mesmo celular por USB e por wifi.
USB = "e2615705"
WIFI = "192.168.1.12:37889"

DOIS = [USB, WIFI]

FICHAS_DOIS = [
    (USB, "Poco X6 Pro", False, "e2615705"),
    (WIFI, "Poco X6 Pro", True, "e2615705"),
]


def _responde(*respostas):
    """
    Simula o usuário digitando. Estoura se for perguntado mais
    vezes que o previsto — laço infinito num prompt é pior que
    um teste vermelho.
    """

    fila = list(respostas)

    def perguntar(_texto):

        if not fila:
            raise AssertionError("perguntou demais")

        return fila.pop(0)

    return perguntar


# =========================================================
# CAMINHOS SEM PERGUNTA
# =========================================================

def test_um_device_nao_pergunta():

    assert devices.escolher([USB], perguntar=_responde()) == USB


def test_serial_fixado_ganha_da_pergunta():
    """
    --device / DEVICE_SERIAL existem justamente para não ver a
    pergunta toda execução.
    """

    escolhido = devices.escolher(
        DOIS,
        pedido=WIFI,
        perguntar=_responde(),
        fichas=FICHAS_DOIS,
    )

    assert escolhido == WIFI


def test_serial_fixado_inexistente_e_erro_claro():

    try:

        devices.escolher(DOIS, pedido="sumiu", fichas=FICHAS_DOIS)

    except RuntimeError as erro:

        # O erro precisa mostrar o que EXISTE, senão o usuário
        # fica adivinhando o serial certo.
        assert USB in str(erro), str(erro)
        assert WIFI in str(erro), str(erro)

        return

    raise AssertionError("deveria ter levantado")


def test_nenhum_device():

    try:

        devices.escolher([], perguntar=_responde())

    except RuntimeError as erro:

        assert "Nenhum" in str(erro), str(erro)

        return

    raise AssertionError("deveria ter levantado")


def test_sem_terminal_nao_trava():
    """
    Sem como perguntar (pipe, cron, IDE sem console), travar num
    input invisível é pior que um erro. A mensagem tem de dizer
    como resolver.
    """

    try:

        devices.escolher(DOIS, perguntar=None, fichas=FICHAS_DOIS)

    except RuntimeError as erro:

        texto = str(erro)

        assert "--device" in texto, texto
        assert "DEVICE_SERIAL" in texto, texto
        assert USB in texto, texto

        return

    raise AssertionError("deveria ter levantado")


# =========================================================
# A PERGUNTA
# =========================================================

def test_escolhe_pelo_numero():

    for resposta, esperado in (("1", USB), ("2", WIFI)):

        escolhido = devices.escolher(
            DOIS,
            perguntar=_responde(resposta),
            fichas=FICHAS_DOIS,
        )

        assert escolhido == esperado, (resposta, escolhido)


def test_enter_aceita_a_sugestao():

    escolhido = devices.escolher(
        DOIS,
        perguntar=_responde(""),
        fichas=FICHAS_DOIS,
    )

    assert escolhido == USB, escolhido


def test_aceita_serial_digitado():
    """
    Colar o serial é natural depois de ler a lista.
    """

    escolhido = devices.escolher(
        DOIS,
        perguntar=_responde(WIFI),
        fichas=FICHAS_DOIS,
    )

    assert escolhido == WIFI


def test_resposta_invalida_pergunta_de_novo():
    """
    O que NÃO pode acontecer: resposta fora da faixa cair em
    algum device por acidente.
    """

    escolhido = devices.escolher(
        DOIS,
        perguntar=_responde("9", "banana", "0", "-1", "2"),
        fichas=FICHAS_DOIS,
    )

    assert escolhido == WIFI, escolhido


def test_pergunta_mostra_os_seriais():

    textos = []

    def perguntar(texto):

        textos.append(texto)

        return "1"

    devices.escolher(
        DOIS,
        perguntar=perguntar,
        fichas=FICHAS_DOIS,
    )

    texto = textos[0]

    assert USB in texto, texto
    assert WIFI in texto, texto

    # Sem o modelo e o USB/wifi, escolher é adivinhar.
    assert "Poco X6 Pro" in texto, texto
    assert "wifi" in texto, texto
    assert "USB" in texto, texto


# =========================================================
# SUGESTÃO E ROTULAGEM
# =========================================================

def test_sugere_usb_sobre_wifi():
    """
    USB por cima de wifi de propósito: a conexão wifi do adb cai
    sozinha e derruba a captura no meio da sessão.
    """

    # USB em segundo lugar na lista.
    fichas = [
        (WIFI, "Poco X6 Pro", True, "e2615705"),
        (USB, "Poco X6 Pro", False, "e2615705"),
    ]

    assert devices.padrao(fichas) == 2, devices.padrao(fichas)

    assert devices.padrao(FICHAS_DOIS) == 1


def test_sugere_o_primeiro_quando_todos_wifi():

    fichas = [
        ("10.0.0.2:5555", "A", True, "aaa"),
        ("10.0.0.3:5555", "B", True, "bbb"),
    ]

    assert devices.padrao(fichas) == 1


def test_marca_o_mesmo_aparelho():
    """
    Duas entradas com o MESMO ro.serialno são o mesmo celular.
    Sem essa marca, a lista parece dois aparelhos diferentes.
    """

    linhas = devices.rotular(FICHAS_DOIS)

    assert "mesmo aparelho" not in linhas[0], linhas[0]
    assert "mesmo aparelho do 1" in linhas[1], linhas[1]


def test_nao_marca_aparelhos_diferentes():

    fichas = [
        (USB, "Poco", False, "aaa"),
        ("outro", "Galaxy", False, "bbb"),
    ]

    for linha in devices.rotular(fichas):

        assert "mesmo aparelho" not in linha, linha


def test_rotula_sem_getprop():
    """
    getprop pode falhar (device lento, sem permissão). A lista
    ainda tem de sair, com o serial, que é o que importa.
    """

    fichas = [
        (USB, None, False, None),
        (WIFI, None, True, None),
    ]

    linhas = devices.rotular(fichas)

    assert USB in linhas[0], linhas[0]
    assert WIFI in linhas[1], linhas[1]

    for linha in linhas:

        assert "None" not in linha, linha


def test_escolha_funciona_sem_getprop():
    """
    getprop falhando não pode impedir a escolha.
    """

    fichas = [
        (USB, None, False, None),
        (WIFI, None, True, None),
    ]

    escolhido = devices.escolher(
        DOIS,
        perguntar=_responde(""),
        fichas=fichas,
    )

    assert escolhido == USB, escolhido


# =========================================================
# O SERIAL CHEGA NA CAPTURA
# =========================================================

def test_captura_usa_o_serial():
    """
    O ponto do bug: sem -s, com dois devices na lista o adb
    recusa TODA chamada com "more than one device".
    """

    from capture.screen import ScreenCapture

    capture = ScreenCapture(USB)

    comando = capture._adb("shell", "ls")

    assert comando[:3] == ["adb", "-s", USB], comando


def test_captura_sem_serial_continua_valendo():
    """
    Um device só: o -s é dispensável, e o construtor antigo
    (sem argumento) tem de continuar funcionando.
    """

    from capture.screen import ScreenCapture

    assert ScreenCapture()._adb("devices") == ["adb", "devices"]


def test_toques_usam_o_serial():

    from actions.android import AndroidActions

    comando = AndroidActions(USB)._base()

    assert comando[:3] == ["adb", "-s", USB], comando


def test_screenshot_usa_o_serial():
    """
    O screencap do template_selector tem o MESMO problema: sem
    -s, "more than one device" e nenhum template novo sai.
    """

    sys.path.insert(0, str(ROOT / "tests"))

    from android_screenshot import AndroidScreenshot

    comando = AndroidScreenshot(serial=USB)._adb(
        "exec-out",
        "screencap",
        "-p",
    )

    assert comando == [
        "adb",
        "-s",
        USB,
        "exec-out",
        "screencap",
        "-p",
    ], comando


def test_screenshot_sem_serial_continua_valendo():

    sys.path.insert(0, str(ROOT / "tests"))

    from android_screenshot import AndroidScreenshot

    comando = AndroidScreenshot()._adb("exec-out")

    assert comando == ["adb", "exec-out"], comando


def test_selector_repassa_o_serial():
    """
    O caminho inteiro: o serial escolhido tem de atravessar o
    TemplateSelector até o screencap.
    """

    sys.path.insert(0, str(ROOT / "tests"))
    sys.path.insert(0, str(ROOT / "tools"))

    from template_selector import TemplateSelector

    selector = TemplateSelector(USB)

    comando = selector.screenshot._adb("exec-out")

    assert comando[:3] == ["adb", "-s", USB], comando


# =========================================================
# RUNNER
# =========================================================

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

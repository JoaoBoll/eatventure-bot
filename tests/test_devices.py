"""
Testes da escolha de device. As fichas são injetadas, nada fala com adb:
o que está sob teste é a DECISÃO de qual serial sai.
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
    """Estoura se perguntado mais vezes que o previsto — laço infinito é pior que teste vermelho."""

    fila = list(respostas)

    def perguntar(_texto):

        if not fila:
            raise AssertionError("perguntou demais")

        return fila.pop(0)

    return perguntar


def test_um_device_nao_pergunta():

    assert devices.escolher([USB], perguntar=_responde()) == USB


def test_serial_fixado_ganha_da_pergunta():
    """--device / DEVICE_SERIAL existem para não ver a pergunta toda execução."""

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

        # O erro precisa mostrar o que EXISTE, senão o usuário adivinha o serial.
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
    """Sem como perguntar (pipe, cron, IDE), travar num input invisível é pior que erro."""

    try:

        devices.escolher(DOIS, perguntar=None, fichas=FICHAS_DOIS)

    except RuntimeError as erro:

        texto = str(erro)

        assert "--device" in texto, texto
        assert "DEVICE_SERIAL" in texto, texto
        assert USB in texto, texto

        return

    raise AssertionError("deveria ter levantado")


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
    """Colar o serial é natural depois de ler a lista."""

    escolhido = devices.escolher(
        DOIS,
        perguntar=_responde(WIFI),
        fichas=FICHAS_DOIS,
    )

    assert escolhido == WIFI


def test_resposta_invalida_pergunta_de_novo():
    """Resposta fora da faixa não pode cair em algum device por acidente."""

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


def test_sugere_usb_sobre_wifi():
    """USB por cima de wifi de propósito: a conexão wifi do adb cai sozinha."""

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
    """Duas entradas com o MESMO ro.serialno são o mesmo celular."""

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
    """getprop pode falhar (device lento, sem permissão); a lista ainda tem de sair."""

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


# Estes testes verificam o "-s <serial>", não o binário do adb: o config
# resolve o caminho completo (PATH ou tools/scrcpy), então comparar com
# o literal "adb" falharia por causa de um caminho como
# "tools/scrcpy/adb.EXE" — que é o caminho CERTO.

def _sem_o_binario(comando):
    """Confere que o comando começa por algum adb e devolve o resto, que é o testado."""

    assert comando, comando

    assert "adb" in Path(comando[0]).name.lower(), comando

    return comando[1:]


def test_captura_usa_o_serial():
    """Sem -s, com dois devices na lista, o adb recusa TODA chamada."""

    from capture.screen import ScreenCapture

    capture = ScreenCapture(USB)

    comando = capture._adb("shell", "ls")

    assert _sem_o_binario(comando)[:2] == ["-s", USB], comando


def test_captura_sem_serial_continua_valendo():
    """Um device só: o -s é dispensável, e o construtor sem argumento continua funcionando."""

    from capture.screen import ScreenCapture

    comando = ScreenCapture()._adb("devices")

    assert _sem_o_binario(comando) == ["devices"], comando


def test_toques_usam_o_serial():

    from actions.android import AndroidActions

    comando = AndroidActions(USB)._base()

    assert _sem_o_binario(comando)[:2] == ["-s", USB], comando


def test_screenshot_usa_o_serial():
    """O screencap do template_selector tem o MESMO problema: sem -s, nenhum template novo sai."""

    sys.path.insert(0, str(ROOT / "tests"))

    from android_screenshot import AndroidScreenshot

    comando = AndroidScreenshot(serial=USB)._adb(
        "exec-out",
        "screencap",
        "-p",
    )

    assert _sem_o_binario(comando) == [
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

    assert _sem_o_binario(comando) == ["exec-out"], comando


def test_selector_repassa_o_serial():
    """O serial escolhido tem de atravessar o TemplateSelector até o screencap."""

    sys.path.insert(0, str(ROOT / "tests"))
    sys.path.insert(0, str(ROOT / "tools"))

    from template_selector import TemplateSelector

    selector = TemplateSelector(USB)

    comando = selector.screenshot._adb("exec-out")

    assert _sem_o_binario(comando)[:2] == ["-s", USB], comando


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

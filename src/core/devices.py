"""Escolha do device Android: USB+wifi do mesmo aparelho aparecem como duas entradas no `adb devices`, então resolve qual serial usar (--device > DEVICE_SERIAL > único conectado > pergunta)."""

import subprocess
import sys

from core import log, config
from core.i18n import t

logger = log.get("devices")
ADB = config.ADB_PATH


def listar():
    """Seriais em estado 'device' — 'unauthorized'/'offline' ficam de fora por não aceitarem comando."""

    resultado = subprocess.run(
        [ADB, "devices"],
        capture_output=True,
        text=True,
        check=True,
    )

    seriais = []

    for linha in resultado.stdout.splitlines():

        linha = linha.strip()

        if not linha or linha.startswith("List of devices"):
            continue

        partes = linha.split()

        if len(partes) >= 2 and partes[1] == "device":

            seriais.append(partes[0])

    return seriais


def _propriedade(serial, nome):
    """getprop, ou None se falhar — puramente informativo, falha não interrompe nada."""

    try:

        resultado = subprocess.run(
            [ADB, "-s", serial, "shell", "getprop", nome],
            capture_output=True,
            text=True,
            timeout=5,
        )

    except (OSError, subprocess.SubprocessError):

        return None

    if resultado.returncode != 0:
        return None

    return resultado.stdout.strip() or None


def descrever(seriais):
    """[(serial, modelo, wifi, hardware)]; `hardware` é o ro.serialno, que revela entradas duplicadas do mesmo aparelho."""

    fichas = []

    for serial in seriais:

        fichas.append(
            (
                serial,
                _propriedade(serial, "ro.product.model"),
                ":" in serial,
                _propriedade(serial, "ro.serialno"),
            )
        )

    return fichas


def rotular(fichas):
    """Uma linha por device; marca o duplicado porque a wifi cai sozinha e derruba a captura no meio da sessão."""

    # hardware -> primeira posição (1-based) em que apareceu
    primeira = {}

    for indice, (_, _, _, hardware) in enumerate(fichas, start=1):

        if hardware and hardware not in primeira:

            primeira[hardware] = indice

    linhas = []

    for indice, ficha in enumerate(fichas, start=1):

        serial, modelo, wifi, hardware = ficha

        partes = [f"{serial:<22}"]

        if modelo:
            partes.append(f"{modelo:<20}")

        partes.append("wifi" if wifi else "USB ")

        if hardware and primeira.get(hardware, indice) != indice:

            partes.append(
                t("messages.duplicate_device_hint", value_1=primeira[hardware])
            )

        linhas.append(f"  {indice} - " + " ".join(partes).rstrip())

    return linhas


def padrao(fichas):
    """Índice (1-based) sugerido: primeiro device por USB — a wifi cai sozinha e derruba a captura."""

    for indice, (_, _, wifi, _) in enumerate(fichas, start=1):

        if not wifi:
            return indice

    return 1


def escolher(seriais, pedido=None, perguntar=None, fichas=None):
    """
    Devolve o serial a usar.

    `pedido`    serial fixado (--device ou config)
    `perguntar` função que recebe o texto e devolve a resposta;
                None = não pergunta (levanta erro em vez disso)
    `fichas`    resultado de descrever(); None = descreve aqui

    Tudo que fala com o mundo entra por parâmetro, para a decisão
    ser testável sem device plugado.
    """

    if not seriais:

        raise RuntimeError(
            t("messages.no_android_devices")
        )

    if pedido:

        if pedido in seriais:
            return pedido

        raise RuntimeError(
            t("messages.device_not_connected", pedido=pedido)
            + "\n".join(f"  {s}" for s in seriais)
        )

    if len(seriais) == 1:
        return seriais[0]

    if fichas is None:
        fichas = descrever(seriais)

    sugerido = padrao(fichas)

    texto = "\n".join(
        [
            "",
            t("messages.multiple_devices"),
            "",
        ]
        + rotular(fichas)
        + [
            "",
            t("messages.device_choice_prompt", sugerido=sugerido),
        ]
    )

    def sem_resposta():

        return RuntimeError(
            t("messages.device_selection_unavailable")
            + "\n".join(rotular(fichas))
            + t("messages.device_selection_help")
        )

    if perguntar is None:

        raise sem_resposta()

    while True:

        # EOF = entrada redirecionada; isatty() sozinho não pega isso no
        # Windows (`python main.py < NUL` passa o teste e só falha aqui).
        try:

            resposta = (perguntar(texto) or "").strip()

        except EOFError:

            raise sem_resposta() from None

        if not resposta:
            return fichas[sugerido - 1][0]

        if resposta.isdigit():

            escolha = int(resposta)

            if 1 <= escolha <= len(fichas):
                return fichas[escolha - 1][0]

            print(t("terminal.choose_device", total=len(fichas)))

            continue

        if resposta in seriais:
            return resposta

        print(t("terminal.device_prompt"))


def resolver(pedido=None):
    """Caminho normal: lista, escolhe (perguntando no terminal se precisar) e loga o escolhido."""

    seriais = listar()

    # Sem terminal, travar num input invisível é pior que um erro claro.
    perguntar = input if sys.stdin and sys.stdin.isatty() else None

    serial = escolher(seriais, pedido=pedido, perguntar=perguntar)

    logger.info(t("messages.device_selected"), serial)

    return serial

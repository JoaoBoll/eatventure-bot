"""
Escolha do device Android.

Com o celular ligado por USB E por wifi, o `adb devices` lista
DOIS entradas para o mesmo aparelho — e daí toda chamada adb sem
`-s` é recusada com "more than one device". Este módulo resolve
qual serial usar, e o serial atravessa o programa inteiro
(captura e toques).

Ordem de precedência:

    1. --device na linha de comando
    2. DEVICE_SERIAL no config
    3. único device conectado
    4. pergunta

A separação entre "listar/descrever" (fala com o adb) e
"escolher" (só decide) existe para a decisão ser testável sem
device nenhum plugado.
"""

import subprocess
import sys

from core import log

logger = log.get("devices")


# =========================================================
# ADB
# =========================================================

def listar():
    """
    Seriais em estado 'device', na ordem em que o adb devolve.

    Estados como 'unauthorized' e 'offline' ficam de fora: não
    aceitam comando, e oferecê-los só geraria erro na frente.
    """

    resultado = subprocess.run(
        ["adb", "devices"],
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
    """
    getprop, ou None se falhar. Puramente informativo, então
    falha não interrompe nada.
    """

    try:

        resultado = subprocess.run(
            ["adb", "-s", serial, "shell", "getprop", nome],
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
    """
    [(serial, modelo, wifi, hardware)] para montar a pergunta.

    `hardware` é o ro.serialno: é ele que revela que duas
    entradas da lista são O MESMO aparelho.
    """

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


# =========================================================
# ROTULAGEM
# =========================================================

def rotular(fichas):
    """
    Uma linha por device, pronta para imprimir.

    Marca o duplicado porque a escolha entre USB e wifi do mesmo
    celular NÃO é indiferente: a wifi cai sozinha e derruba a
    captura no meio da sessão.
    """

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
                f"(mesmo aparelho do {primeira[hardware]})"
            )

        linhas.append(f"  {indice} - " + " ".join(partes).rstrip())

    return linhas


def padrao(fichas):
    """
    Índice (1-based) sugerido: o primeiro device por USB.

    USB por cima de wifi de propósito — a conexão wifi do adb
    cai sozinha, e quando cai a captura morre no meio da sessão.
    """

    for indice, (_, _, wifi, _) in enumerate(fichas, start=1):

        if not wifi:
            return indice

    return 1


# =========================================================
# ESCOLHA
# =========================================================

def escolher(seriais, pedido=None, perguntar=None, fichas=None):
    """
    Devolve o serial a usar.

    `pedido`    serial fixado (--device ou config)
    `perguntar` função que recebe o texto e devolve a resposta;
                None = não pergunta (levanta erro em vez disso)
    `fichas`    resultado de descrever(); None = descreve aqui

    Função pura em relação à decisão: tudo que fala com o mundo
    entra por parâmetro, então o comportamento é testável sem
    device plugado.
    """

    if not seriais:

        raise RuntimeError(
            "Nenhum dispositivo Android encontrado.\n"
            "Confira o cabo, a depuração USB e 'adb devices'."
        )

    # -----------------------------------------------------
    # Serial fixado
    # -----------------------------------------------------

    if pedido:

        if pedido in seriais:
            return pedido

        raise RuntimeError(
            f"Dispositivo '{pedido}' não está conectado.\n"
            "Disponíveis:\n"
            + "\n".join(f"  {s}" for s in seriais)
        )

    # -----------------------------------------------------
    # Um só
    # -----------------------------------------------------

    if len(seriais) == 1:
        return seriais[0]

    # -----------------------------------------------------
    # Pergunta
    # -----------------------------------------------------

    if fichas is None:
        fichas = descrever(seriais)

    sugerido = padrao(fichas)

    texto = "\n".join(
        [
            "",
            "Mais de um dispositivo conectado:",
            "",
        ]
        + rotular(fichas)
        + [
            "",
            f"Qual usar? [{sugerido}]: ",
        ]
    )

    def sem_resposta():

        return RuntimeError(
            "Mais de um dispositivo conectado e não há como "
            "perguntar (entrada não interativa).\n"
            + "\n".join(rotular(fichas))
            + "\n\nUse --device SERIAL ou defina DEVICE_SERIAL "
            "no config."
        )

    if perguntar is None:

        raise sem_resposta()

    while True:

        # EOF em vez de resposta: entrada redirecionada.
        #
        # O isatty() sozinho NÃO pega este caso no Windows — o
        # NUL é um dispositivo de caractere, então
        # `python main.py < NUL` passa pelo teste do isatty e
        # só falharia aqui, com um EOFError que não explica
        # nada.
        try:

            resposta = (perguntar(texto) or "").strip()

        except EOFError:

            raise sem_resposta() from None

        # ENTER aceita a sugestão.
        if not resposta:
            return fichas[sugerido - 1][0]

        # Aceita o número da lista...
        if resposta.isdigit():

            escolha = int(resposta)

            if 1 <= escolha <= len(fichas):
                return fichas[escolha - 1][0]

            print(f"Escolha entre 1 e {len(fichas)}.")

            continue

        # ...ou o serial digitado/colado.
        if resposta in seriais:
            return resposta

        print("Número da lista ou serial completo.")


def resolver(pedido=None):
    """
    O caminho normal: lista, escolhe (perguntando no terminal se
    precisar) e loga o que foi escolhido.
    """

    seriais = listar()

    # Sem terminal (rodando por cron, pipe, IDE sem console)
    # não há como perguntar, e travar num input invisível é
    # pior que um erro claro.
    perguntar = input if sys.stdin and sys.stdin.isatty() else None

    serial = escolher(seriais, pedido=pedido, perguntar=perguntar)

    logger.info("Dispositivo: %s", serial)

    return serial

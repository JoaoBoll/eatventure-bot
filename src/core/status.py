"""
Painel de status: um bloco fixo, reescrito no lugar.

--------------------------------------------------------------
O PROBLEMA QUE ELE RESOLVE
--------------------------------------------------------------

O log conta a HISTÓRIA: cada ação, cada troca de estado, cada
ciclo fechado, uma linha por evento. Isso é o que se quer quando
algo deu errado e você vai ler depois.

Não é o que se quer quando você está OLHANDO. Com o bot agindo
~1x/s, o terminal rola sem parar e a pergunta simples — "em que
device ele está, quantas vezes já voou, o que está fazendo
agora" — exige ler linhas que já subiram.

O painel responde essa pergunta e só ela. Ele não acumula: são
sempre as mesmas linhas, reescritas no mesmo lugar. Nada de
spam, porque nada é impresso duas vezes.

--------------------------------------------------------------
CONVIVER COM O LOG
--------------------------------------------------------------

Painel e log disputam o mesmo terminal: qualquer linha de log
empurra o painel para cima, e a reescrita passa a apagar a
linha errada.

Não tem como resolver isso só aqui, e por isso o main BAIXA o
log do console para WARNING quando o painel está ligado. Aviso e
erro continuam aparecendo (eles interessam mais que o painel, e
são raros o bastante para não bagunçar); o INFO de cada ação
sai, porque é justamente o que o painel substitui.

Para ver o log detalhado de novo, desligue STATUS_PANEL no
config.
"""

import os
import platform
import shutil
import sys
import time

from core.config import (
    BATTERY_POLL_INTERVAL,
    DETECTOR_DEBUG_MISSES,
)
from core.metrics import formata_duracao


def formata_bateria(leitura):
    """
    A leitura do BatteryMonitor em uma palavra curta.

    `leitura` é (nível, carregando, idade_da_leitura) — ou None
    antes da primeira medição.

    Marca a leitura VELHA em vez de esconder: um número parado
    parece atual, e é assim que se passa uma hora sem perceber
    que o adb travou. O intervalo de medição é conhecido, então
    passar do dobro dele já é sinal.
    """

    if not leitura:
        return "bateria ?"

    nivel, carregando, idade = leitura

    if nivel is None:
        return "bateria ?"

    texto = f"bateria {nivel}%"

    if carregando:
        return texto + " carregando"

    if idade and idade > BATTERY_POLL_INTERVAL * 2:
        texto += f" ({idade:.0f}s atras)"

    return texto


# Quantas linhas o bloco ocupa. Fixo de propósito: a reescrita
# precisa saber quantas linhas subir, e descobrir isso contando
# o que foi impresso é como o desenho sai torto.
#
# A linha de quase-acerto é a única opcional, e a decisão é
# tomada UMA vez, na construção do painel — não a cada
# redesenho. Uma linha que entra e sai deslocaria o bloco e o
# `\033[nA` passaria a subir a conta errada.
LINHAS_BASE = 5


class StatusPanel:
    """
    Bloco de status reescrito no lugar.

    Use `update(...)` a cada volta do loop — ele decide sozinho
    se já é hora de redesenhar.
    """

    def __init__(
        self,
        device,
        interval=0.5,
        stream=None,
        misses_line=None,
    ):

        self.device = device or "?"

        # A linha "Sem deteccao" só faz sentido com
        # DETECTOR_DEBUG_MISSES ligado: sem ele o detector nem
        # calcula o melhor match, e a linha ficaria eternamente
        # em "—" ocupando espaço.
        self.misses_line = (
            DETECTOR_DEBUG_MISSES
            if misses_line is None
            else misses_line
        )

        self.linhas = LINHAS_BASE + (
            1 if self.misses_line else 0
        )

        # Redesenhar a 30 fps não deixa ninguém mais informado e
        # gasta syscall de escrita. Meio segundo é rápido o
        # bastante para parecer vivo.
        self.interval = interval

        self.stream = stream or sys.stdout

        self.desenhado = False
        self.ultimo = 0.0

        self.iniciado = time.monotonic()

        # Terminal que não entende ANSI (arquivo, pipe, log de
        # CI) não pode receber sequência de cursor: viraria
        # lixo no meio do texto. Aí o painel imprime uma vez e
        # se cala.
        self.ansi = self._suporta_ansi()

    # -----------------------------------------------------
    # TERMINAL
    # -----------------------------------------------------

    def _suporta_ansi(self):
        """
        O terminal aceita mover o cursor?
        """

        if not hasattr(self.stream, "isatty"):
            return False

        try:
            if not self.stream.isatty():
                return False

        except Exception:
            return False

        if platform.system() != "Windows":
            return True

        # No Windows o console só interpreta ANSI depois de
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING. O Windows Terminal
        # já vem com isso; o console legado, não — e sem ligar,
        # o painel apareceria como "←[2K" na tela.
        try:

            import ctypes

            kernel32 = ctypes.windll.kernel32

            # -11 = STD_OUTPUT_HANDLE, 0x0004 = VT processing
            handle = kernel32.GetStdHandle(-11)

            modo = ctypes.c_uint32()

            if not kernel32.GetConsoleMode(
                handle,
                ctypes.byref(modo),
            ):
                return False

            return bool(
                kernel32.SetConsoleMode(
                    handle,
                    modo.value | 0x0004,
                )
            )

        except Exception:
            return False

    # -----------------------------------------------------
    # CONTEÚDO
    # -----------------------------------------------------

    def _largura(self):
        """
        Largura útil do terminal.

        Importa mais do que parece: linha mais comprida que o
        terminal QUEBRA em duas, o bloco passa a ocupar 7 linhas
        e o `\033[6A` sobe pouco — o painel começa a se
        reescrever em cima de si mesmo. Cortar é feio; quebrar
        estraga o desenho todo.
        """

        try:
            colunas = shutil.get_terminal_size().columns

        except Exception:
            return 100

        # -1: escrever na última coluna já provoca a quebra em
        # alguns terminais.
        return max(20, colunas - 1)

    def _linhas(self, resumo, extra=None, misses=None):

        acao = resumo.get("acao") or "—"

        acao_ha = resumo.get("acao_ha")

        # "há quanto tempo" é o que diferencia bot trabalhando
        # de bot parado numa ação que deu certo uma vez.
        if acao_ha is not None and acao_ha > 1.0:
            acao = f"{acao}  ({acao_ha:.0f}s atrás)"

        corrido = time.monotonic() - self.iniciado

        linhas = [
            f"Dispositivo conectado: {self.device}",
            f"Voou: {resumo.get('voos', 0)} vezes",
            f"Renovou: {resumo.get('reformas', 0)} vezes",
            f"Ação: {acao}",
        ]

        rodape = (
            f"estado {resumo.get('estado', '?')}"
            f" | {resumo.get('acoes', 0)} ações"
            f" | {formata_duracao(corrido)} rodando"
        )

        if extra:
            rodape += f" | {extra}"

        linhas.append(rodape)

        # -------------------------------------------------
        # QUASE-ACERTO
        # -------------------------------------------------
        #
        # Só existe com o debug ligado. Ligada, aparece sempre —
        # mesmo sem nada a relatar — porque o bloco tem altura
        # fixa e uma linha intermitente estragaria a reescrita.
        if self.misses_line:

            linhas.append(
                f"Sem deteccao: {misses}"
                if misses
                else "Sem deteccao: —"
            )

        # Trava o tamanho: se um dia alguém acrescentar uma
        # linha sem mexer na conta, o painel comeria a linha de
        # cima em vez de falhar visivelmente.
        assert len(linhas) == self.linhas, (
            len(linhas),
            self.linhas,
        )

        largura = self._largura()

        return [
            linha
            if len(linha) <= largura
            else linha[: largura - 1] + "…"
            for linha in linhas
        ]

    # -----------------------------------------------------
    # DESENHO
    # -----------------------------------------------------

    def update(self, resumo, extra=None, misses=None, force=False):
        """
        Redesenha se já passou o intervalo.

        `resumo` é o dict de StateMachine.summary().
        `misses` é o texto de Detector.miss_report().
        """

        agora = time.monotonic()

        if not force and agora - self.ultimo < self.interval:
            return

        self.ultimo = agora

        linhas = self._linhas(resumo, extra, misses)

        if not self.ansi:

            # Sem ANSI, imprime UMA vez e para. Melhor um bloco
            # no começo do arquivo que o mesmo bloco repetido
            # mil vezes.
            if not self.desenhado:

                self.stream.write("\n".join(linhas) + "\n")
                self.stream.flush()

                self.desenhado = True

            return

        saida = []

        if self.desenhado:

            # Sobe ao topo do bloco anterior.
            saida.append(f"\033[{self.linhas}A")

        for linha in linhas:

            # \033[2K limpa a linha inteira: sem isso, um texto
            # curto deixa o rabo do texto anterior na tela.
            saida.append(f"\r\033[2K{linha}\n")

        self.stream.write("".join(saida))
        self.stream.flush()

        self.desenhado = True

    # -----------------------------------------------------

    def close(self):
        """
        Deixa o cursor abaixo do bloco, para o que vier depois
        (log de encerramento, traceback) não escrever em cima.
        """

        if self.desenhado and self.ansi:

            self.stream.write("\n")
            self.stream.flush()

        self.desenhado = False

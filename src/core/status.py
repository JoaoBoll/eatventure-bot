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
import sys
import time

from core.config import BATTERY_POLL_INTERVAL
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
LINHAS = 5


class StatusPanel:
    """
    Bloco de status reescrito no lugar.

    Use `update(...)` a cada volta do loop — ele decide sozinho
    se já é hora de redesenhar.
    """

    def __init__(self, device, interval=0.5, stream=None):

        self.device = device or "?"

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

        if os.name != "nt":
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

    def _linhas(self, resumo, extra=None):

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

        # Trava o tamanho: se um dia alguém acrescentar uma
        # linha sem mexer em LINHAS, o painel comeria a linha
        # de cima em vez de falhar visivelmente.
        assert len(linhas) == LINHAS, (len(linhas), LINHAS)

        return linhas

    # -----------------------------------------------------
    # DESENHO
    # -----------------------------------------------------

    def update(self, resumo, extra=None, force=False):
        """
        Redesenha se já passou o intervalo.

        `resumo` é o dict de StateMachine.summary().
        """

        agora = time.monotonic()

        if not force and agora - self.ultimo < self.interval:
            return

        self.ultimo = agora

        linhas = self._linhas(resumo, extra)

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
            saida.append(f"\033[{LINHAS}A")

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

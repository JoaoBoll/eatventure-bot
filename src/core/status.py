"""Status e último evento em uma tela do terminal, sem acumular linhas."""

import os
from core.i18n import display_code, t
import platform
import shutil
import sys
import threading
import time
import unicodedata

from core.config import (
    BATTERY_POLL_INTERVAL,
    DETECTOR_DEBUG_MISSES,
)
from core.metrics import formata_duracao


def formata_bateria(leitura):
    """
    A leitura do BatteryMonitor (nível, carregando, idade) em palavra curta.

    Marca a leitura VELHA em vez de esconder — um número parado parece
    atual, e é assim que se passa uma hora sem perceber que o adb travou.
    """

    if not leitura:
        return t("messages.battery_unknown")

    nivel, carregando, idade = leitura

    if nivel is None:
        return t("messages.battery_unknown")

    texto = t("messages.battery_status", nivel=nivel)

    if carregando:
        return texto + t("messages.charging_suffix")

    if idade and idade > BATTERY_POLL_INTERVAL * 2:
        texto += t("messages.battery_age", idade=idade)

    return texto


LINHAS_BASE = 6


class StatusPanel:
    """Bloco de status reescrito no lugar; `update(...)` decide sozinho quando redesenhar."""

    def __init__(
        self,
        device,
        interval=0.5,
        stream=None,
        misses_line=None,
        templates=0,
    ):

        self.device = device or "?"

        # "Sem deteccao" só faz sentido com DETECTOR_DEBUG_MISSES ligado
        # (sem ele o detector nem calcula o melhor match).
        self.misses_line = (
            DETECTOR_DEBUG_MISSES
            if misses_line is None
            else misses_line
        )

        self.linhas = LINHAS_BASE + (
            1 if self.misses_line else 0
        )

        self.interval = interval

        self.stream = stream or sys.stdout

        self.desenhado = False
        self.ultimo = 0.0
        self.lock = threading.RLock()
        self._closed = False
        self._resumo = {"estado": "iniciando", "templates": templates}
        self._extra = None
        self._misses = None
        self._mensagem = t("messages.waiting_events")

        self.iniciado = time.monotonic()

        # Sem ANSI (arquivo, pipe, log de CI) a sequência de cursor
        # viraria lixo no meio do texto; aí o painel imprime uma vez e se cala.
        self.ansi = self._suporta_ansi()

    def _suporta_ansi(self):

        if os.environ.get("TERM") == "dumb":
            return False

        if not hasattr(self.stream, "isatty"):
            return False

        try:
            if not self.stream.isatty():
                return False

        except Exception:
            return False

        if platform.system() != "Windows":
            return True

        # O console legado do Windows só interpreta ANSI depois de
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING; sem ligar, o painel
        # apareceria como "←[2K" na tela.
        try:

            import ctypes
            from ctypes import wintypes
            import msvcrt

            kernel32 = ctypes.windll.kernel32
            kernel32.GetConsoleMode.argtypes = (
                wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
            )
            kernel32.GetConsoleMode.restype = wintypes.BOOL
            kernel32.SetConsoleMode.argtypes = (
                wintypes.HANDLE, wintypes.DWORD,
            )
            kernel32.SetConsoleMode.restype = wintypes.BOOL

            handle = msvcrt.get_osfhandle(self.stream.fileno())
            modo = wintypes.DWORD()

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

    def _largura(self):
        return max(1, shutil.get_terminal_size(fallback=(100, 24)).columns - 1)

    @staticmethod
    def _cortar(texto, largura):
        caracteres = [
            (caractere, 0 if unicodedata.combining(caractere) else (
                2 if unicodedata.east_asian_width(caractere) in ("W", "F") else 1
            ))
            for caractere in str(texto).replace("\n", " ").replace("\r", " ")
            if not unicodedata.category(caractere).startswith("C")
        ]

        if sum(tamanho for _, tamanho in caracteres) <= largura:
            return "".join(caractere for caractere, _ in caracteres)

        resultado = []
        ocupado = 0
        for caractere, tamanho in caracteres:
            if ocupado + tamanho > largura - 1:
                break
            resultado.append(caractere)
            ocupado += tamanho

        return "".join(resultado) + "…"

    def _linhas(self, resumo, extra=None, misses=None):

        acao = display_code("actions", resumo.get("acao")) or "—"

        acao_ha = resumo.get("acao_ha")

        # "há quanto tempo" é o que diferencia bot trabalhando
        # de bot parado numa ação que deu certo uma vez.
        if acao_ha is not None and acao_ha > 1.0:
            acao = t("messages.action_age", acao=acao, acao_ha=acao_ha)

        corrido = time.monotonic() - self.iniciado

        linhas = [
            t("messages.connected_device", value_1=self.device),
            t("messages.template_count", value_1=resumo.get('templates', 0)),
            t("messages.flight_count", value_1=resumo.get('voos', 0)),
            t("messages.renovation_count", value_1=resumo.get('reformas', 0)),
            t("messages.current_action", acao=acao),
        ]
        if resumo.get("vision_error"):
            linhas.append(
                t("messages.vision_failures", value_1=resumo['vision_error'], value_2=resumo.get('vision_failures', 0))
            )

        rodape = (
            t("messages.runtime_summary", value_1=display_code('states', resumo.get('estado', '?')), value_2=resumo.get('acoes', 0), value_3=formata_duracao(corrido))
        )

        if extra:
            rodape += f" | {extra}"

        linhas.append(rodape)

        if self.misses_line:

            linhas.append(
                t("messages.detection_misses", misses=misses)
                if misses
                else t("messages.no_detection_misses")
            )

        assert len(linhas) == self.linhas, (
            len(linhas),
            self.linhas,
        )

        largura = self._largura()

        return [
            self._cortar(linha, largura)
            for linha in linhas
        ]

    def update(self, resumo, extra=None, misses=None, force=False):
        """Redesenha se já passou o intervalo. `resumo` é StateMachine.summary(), `misses` é Detector.miss_report()."""

        with self.lock:
            if self._closed:
                return
            self._resumo = dict(resumo)
            self._extra = extra
            self._misses = misses
            self._redesenhar(force)

    def write_log(self, mensagem, force=False):
        with self.lock:
            if self._closed or not self.ansi:
                return False
            self._mensagem = mensagem
            self._redesenhar(force)
            return True

    def _redesenhar(self, force=False):
        agora = time.monotonic()
        if not force and agora - self.ultimo < self.interval:
            return
        self.ultimo = agora

        linhas = self._linhas(self._resumo, self._extra, self._misses)
        if not self.ansi:
            if not self.desenhado:
                self.stream.write("\n".join(linhas) + "\n")
                self.stream.flush()
                self.desenhado = True
            return

        altura = max(1, shutil.get_terminal_size(fallback=(100, 24)).lines - 1)
        linhas.extend(("", t("messages.latest_log")))
        disponiveis = max(0, altura - len(linhas))
        if disponiveis:
            linhas.extend(
                self._cortar(linha, self._largura())
                for linha in self._mensagem.splitlines()[-disponiveis:]
            )

        # A tela alternativa evita que o redimensionamento reordene o histórico.
        prefixo = "" if self.desenhado else "\033[?1049h\033[?25l"
        saida = "\n".join(f"\r\033[2K{linha}" for linha in linhas[:altura])
        self.stream.write(prefixo + "\033[H" + saida + "\033[J")
        self.stream.flush()
        self.desenhado = True

    def close(self):
        with self.lock:
            if self._closed:
                return
            self._closed = True
            if self.desenhado and self.ansi:
                self.stream.write("\033[?25h\033[?1049l")
                linhas = self._linhas(self._resumo, self._extra, self._misses)
                self.stream.write("\n".join(linhas) + "\n" + self._mensagem + "\n")
                self.stream.flush()
            self.desenhado = False

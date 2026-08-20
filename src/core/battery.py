"""
Leitura da bateria do device, fora do caminho crítico.

`dumpsys battery` custa ~56 ms — mais de 3x uma passada inteira
do detector no estado UPGRADE. Chamar isso no loop de render
derrubaria o FPS da janela para pagar por um número que muda de
1% a cada vários minutos.

Então roda numa thread própria, num intervalo folgado, e o loop
só lê o último valor em memória.
"""

import threading
import time

from core import log
from core.config import BATTERY_POLL_INTERVAL

logger = log.get("battery")


class BatteryMonitor:

    def __init__(self, android, interval=BATTERY_POLL_INTERVAL):

        self.android = android
        self.interval = interval

        self.running = False
        self.thread = None

        # Última leitura. None = ainda não leu, ou o adb falhou
        # e nunca deu certo.
        self.level = None
        self.charging = False

        # Quando a leitura foi feita. Serve para o HUD poder
        # dizer "velha" em vez de mentir um número parado.
        self.updated = 0.0

        self.lock = threading.Lock()

        # Acorda a thread na hora de parar, em vez de esperar o
        # intervalo inteiro terminar.
        self.wake = threading.Event()

    # -----------------------------------------------------
    # Ciclo de vida
    # -----------------------------------------------------

    def start(self):

        if self.running:
            return

        self.running = True

        self.wake.clear()

        self.thread = threading.Thread(
            target=self._run,
            name="battery",
            daemon=True,
        )

        self.thread.start()

    def stop(self):

        self.running = False

        # Sem isto, encerrar o bot esperaria até
        # BATTERY_POLL_INTERVAL segundos.
        self.wake.set()

        if self.thread:

            self.thread.join(timeout=2.0)

            self.thread = None

    # -----------------------------------------------------
    # Leitura
    # -----------------------------------------------------

    def get(self):
        """
        (porcentagem, carregando, idade_em_segundos)

        porcentagem é None enquanto não houver leitura.
        """

        with self.lock:

            if self.level is None:
                return None, False, 0.0

            return (
                self.level,
                self.charging,
                time.monotonic() - self.updated,
            )

    # -----------------------------------------------------
    # Thread
    # -----------------------------------------------------

    def _run(self):

        while self.running:

            leitura = self.android.battery()

            if leitura is not None:

                nivel, carregando = leitura

                with self.lock:

                    anterior = self.level

                    self.level = nivel
                    self.charging = carregando
                    self.updated = time.monotonic()

                if anterior != nivel:

                    logger.info(
                        "Bateria: %d%%%s",
                        nivel,
                        " (carregando)" if carregando else "",
                    )

            # Espera interrompível.
            self.wake.wait(self.interval)

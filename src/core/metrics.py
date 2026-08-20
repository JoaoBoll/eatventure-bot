"""
Medidores de taxa e de duração.

Ficam num módulo só porque a captura e o detector precisam
da mesma conta, e duplicar a conta é como as duas metades
divergem.
"""

import threading
import time
from collections import deque


class RateMeter:
    """
    Eventos por segundo, em janela deslizante.

    Janela deslizante em vez de média desde o início: o que
    interessa é a taxa AGORA. Uma média acumulada esconde o
    momento em que o stream engasgou.
    """

    def __init__(self, window=2.0):

        self.window = window

        self.timestamps = deque()

        self.lock = threading.Lock()

    def tick(self):

        now = time.monotonic()

        with self.lock:

            self.timestamps.append(now)

            self._trim(now)

    def rate(self):

        now = time.monotonic()

        with self.lock:

            self._trim(now)

            if len(self.timestamps) < 2:
                return 0.0

            elapsed = self.timestamps[-1] - self.timestamps[0]

            if elapsed <= 0:
                return 0.0

            # n eventos delimitam n-1 intervalos.
            return (len(self.timestamps) - 1) / elapsed

    def _trim(self, now):

        limit = now - self.window

        while self.timestamps and self.timestamps[0] < limit:

            self.timestamps.popleft()


class DurationMeter:
    """
    Média móvel exponencial de duração.

    Barata (um float) e não precisa de histórico.
    """

    def __init__(self, smoothing=0.2):

        self.smoothing = smoothing

        self.value = 0.0

        self.lock = threading.Lock()

    def add(self, duration):

        with self.lock:

            if self.value <= 0.0:

                self.value = duration

            else:

                self.value = (
                    self.smoothing * duration
                    + (1.0 - self.smoothing) * self.value
                )

    def average(self):

        with self.lock:
            return self.value


def formata_duracao(segundos):
    """
    Duração legível de relance: "42s", "3m07s", "1h04m".

    Segundo cheio, nunca decimal: aqui a leitura é de canto de
    olho, e "127.4s" obriga a fazer conta de cabeça.
    """

    if segundos is None:
        return "--"

    total = int(max(0, segundos))

    if total < 60:
        return f"{total}s"

    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"

    return f"{total // 3600}h{(total % 3600) // 60:02d}m"

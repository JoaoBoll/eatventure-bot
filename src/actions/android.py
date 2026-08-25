"""
Camada mais baixa: comandos adb no device.

Nada aqui derruba o bot. Um adb que engasgou devolve
False e vira WARNING — antes qualquer falha transitória
levantava CalledProcessError e matava o programa.
"""

import re
import subprocess

from core import log, config

logger = log.get("android")
ADB = config.ADB_PATH


class AndroidActions:

    def __init__(self, serial=None):

        self.serial = serial

    # =====================================================
    # COMANDO
    # =====================================================

    def _base(self):

        command = [ADB]

        if self.serial:

            command += ["-s", self.serial]

        return command

    def _run(self, *args, timeout=10.0):
        """
        Executa um comando adb. Devolve stdout ou None.
        """

        command = self._base() + list(args)

        try:

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

        except subprocess.TimeoutExpired:

            logger.warning(
                "adb travou (timeout %.1fs): %s",
                timeout,
                " ".join(args),
            )

            return None

        except OSError as error:

            logger.warning(
                "adb indisponível: %s",
                error,
            )

            return None

        if result.returncode != 0:

            logger.warning(
                "adb falhou (%d): %s | %s",
                result.returncode,
                " ".join(args),
                (result.stderr or "").strip(),
            )

            return None

        return result.stdout

    # =====================================================
    # BATERIA
    # =====================================================

    def battery(self, timeout=5.0):
        """
        (porcentagem, carregando) ou None se o adb não
        respondeu.

        Timeout curto de propósito: isto é informação de HUD.
        Se o device engasgar, é melhor a leitura envelhecer do
        que a thread ficar presa 10 s.
        """

        output = self._run(
            "shell",
            "dumpsys",
            "battery",
            timeout=timeout,
        )

        if not output:
            return None

        campos = {}

        for linha in output.splitlines():

            if ":" not in linha:
                continue

            chave, _, valor = linha.partition(":")

            campos[chave.strip()] = valor.strip()

        try:

            level = int(campos["level"])

            # A escala nem sempre é 100. Assumir que é daria
            # "level 17" virando 17% num device de escala 255.
            scale = int(campos.get("scale", 100)) or 100

        except (KeyError, ValueError):

            logger.warning("dumpsys battery sem 'level' legível")

            return None

        # status 2 = charging, 5 = full. As flags de energia
        # cobrem o caso de estar plugado sem contar como
        # 'charging' (bateria cheia, ou carga pausada).
        carregando = campos.get("status") in ("2", "5") or any(
            campos.get(f"{fonte} powered") == "true"
            for fonte in ("AC", "USB", "Wireless", "Dock")
        )

        return round(level * 100.0 / scale), carregando

    # =====================================================
    # TAMANHO DA TELA
    # =====================================================

    def get_screen_size(self):
        """
        Resolução real do device, via 'wm size'.

        É o que permite converter coordenada de frame em
        coordenada de toque quando o stream vem reduzido.
        """

        output = self._run("shell", "wm", "size")

        if not output:
            return None

        # Prefere "Override size" quando existe: é a
        # resolução em uso.
        match = None

        for line in output.splitlines():

            found = re.search(
                r"(\d+)x(\d+)",
                line,
            )

            if not found:
                continue

            match = found

            if "Override" in line:
                break

        if not match:
            return None

        return (
            int(match.group(1)),
            int(match.group(2)),
        )

    # =====================================================
    # CLICK
    # =====================================================

    def click(self, x, y):

        return self._run(
            "shell",
            "input",
            "tap",
            str(int(x)),
            str(int(y)),
        ) is not None

    def tap_many(self, x, y, times, timeout=None):
        """
        N toques no mesmo ponto, em UM comando adb.

        Antes eram N chamadas `adb shell input tap`, cada
        uma pagando spawn do cliente adb, round trip ao
        servidor e uma JVM no device (o `input` é um script
        que sobe o app_process). Com 5 toques isso passava
        de um segundo — para uma ação que deveria ser
        instantânea.

        O espaçamento entre os toques não precisa de sleep
        nosso: cada `input tap` leva ~60-100 ms para subir
        no device, e é esse tempo que separa um toque do
        outro. Um sleep em Python só somaria em cima.
        """

        vezes = max(1, int(times))

        if vezes == 1:
            return self.click(x, y)

        um = f"input tap {int(x)} {int(y)}"

        return self._run(
            "shell",
            "; ".join([um] * vezes),

            # O adb tem de sobreviver aos N toques.
            timeout=(
                timeout
                if timeout is not None
                else 5.0 + 0.5 * vezes
            ),
        ) is not None

    def swipe_many(self, x1, y1, x2, y2, times, duration=300):
        """
        N swipes iguais, em UM comando adb.

        Mesmo motivo do tap_many. A rolagem até o fim eram
        6 processos adb; agora é um.
        """

        vezes = max(1, int(times))

        um = (
            f"input swipe {int(x1)} {int(y1)} "
            f"{int(x2)} {int(y2)} {int(duration)}"
        )

        return self._run(
            "shell",
            "; ".join([um] * vezes),

            timeout=(duration / 1000.0 + 2.0) * vezes + 5.0,
        ) is not None

    # =====================================================
    # PRESS / LONG PRESS
    # =====================================================

    def press(self, x, y, duration=4.0):

        duration_ms = int(duration * 1000)

        logger.debug(
            "long press (%d, %d) -> %.1fs",
            x,
            y,
            duration,
        )

        return self._run(
            "shell",
            "input",
            "swipe",
            str(int(x)),
            str(int(y)),
            str(int(x)),
            str(int(y)),
            str(duration_ms),

            # O adb precisa sobreviver ao press inteiro.
            timeout=duration + 5.0,
        ) is not None

    # =====================================================
    # SWIPE
    # =====================================================

    def swipe(self, x1, y1, x2, y2, duration=300):

        return self._run(
            "shell",
            "input",
            "swipe",
            str(int(x1)),
            str(int(y1)),
            str(int(x2)),
            str(int(y2)),
            str(int(duration)),

            timeout=duration / 1000.0 + 5.0,
        ) is not None

    # =====================================================
    # BACK
    # =====================================================

    def back(self):

        return self._run(
            "shell",
            "input",
            "keyevent",
            "4",
        ) is not None

"""
Camada mais baixa: comandos adb no device.

Nada aqui derruba o bot. Um adb que engasgou devolve
False e vira WARNING — antes qualquer falha transitória
levantava CalledProcessError e matava o programa.
"""

import re
import subprocess

from core import log

logger = log.get("android")


class AndroidActions:

    def __init__(self, serial=None):

        self.serial = serial

    # =====================================================
    # COMANDO
    # =====================================================

    def _base(self):

        command = ["adb"]

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

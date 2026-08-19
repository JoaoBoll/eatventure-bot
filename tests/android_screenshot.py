"""
Screenshot do device via adb.

Duas pastas, de propósito:

    tests/capture/   captura de trabalho, sobrescrita à
                     vontade, fora do git
    tests/images/    fixtures do teste de regressão,
                     versionados

O template_selector grava na PRIMEIRA. Ele antes gravava
sempre em tests/images/screen.png — ou seja, cada template
recortado sobrescrevia em silêncio o fixture em que o
tests/test_detection.py se baseia, invalidando a linha de
base sem ninguém notar.

Uso:

    python tests/android_screenshot.py
        -> tests/capture/screen.png (descartável)

    python tests/android_screenshot.py --fixture nome.png
        -> tests/images/nome.png (entra na regressão)
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "src"))

from core import devices, log                     # noqa: E402
from core.config import DEVICE_SERIAL, LOG_LEVEL  # noqa: E402

# Fixtures do teste de regressão. Versionados.
IMAGES_DIR = ROOT / "tests" / "images"

# Capturas de trabalho. Descartáveis.
CAPTURE_DIR = ROOT / "tests" / "capture"


class AndroidScreenshot:

    def __init__(self, output_dir=None, serial=None):

        # Device escolhido. None = deixa o adb decidir, o que só
        # funciona com UM device conectado.
        self.serial = serial

        # Relativo ao arquivo, não ao diretório de trabalho.
        self.output_dir = (
            CAPTURE_DIR
            if output_dir is None
            else Path(output_dir)
        )

        self.output_dir.mkdir(
            parents=True,
            exist_ok=True
        )

    def _adb(self, *args):
        """
        Comando adb já apontado para o device escolhido.

        Sem o -s, com dois devices na lista (o mesmo celular por
        USB e por wifi, por exemplo) o adb recusa a captura com
        "more than one device".
        """

        comando = ["adb"]

        if self.serial:

            comando += ["-s", self.serial]

        return comando + list(args)

    def capture(self, filename="screen.png"):

        output_path = self.output_dir / filename

        result = subprocess.run(
            self._adb(
                "exec-out",
                "screencap",
                "-p",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "Erro ao capturar tela:\n"
                + result.stderr.decode(
                    errors="replace"
                )
            )

        output_path.write_bytes(
            result.stdout
        )

        print(
            f"Screenshot salvo em: {output_path}"
        )

        return output_path


# =========================================================
# Main
# =========================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Captura a tela do device. Sem --fixture, grava "
            "em tests/capture/ (descartável)."
        )
    )

    parser.add_argument(
        "--fixture",
        metavar="NOME.png",
        help=(
            "grava em tests/images/ para entrar no teste "
            "de regressão"
        ),
    )

    parser.add_argument(
        "--name",
        default="screen.png",
        help="nome do arquivo de trabalho",
    )

    parser.add_argument(
        "--device",
        metavar="SERIAL",
        help="serial do device (adb devices)",
    )

    args = parser.parse_args()

    log.setup(LOG_LEVEL)

    serial = devices.resolver(args.device or DEVICE_SERIAL)

    if args.fixture:

        screenshot = AndroidScreenshot(IMAGES_DIR, serial)

        screenshot.capture(args.fixture)

        print()
        print("Fixture novo. Rode agora:")
        print("  python tests/test_detection.py --update")
        print("e CONFIRA o diff antes de comitar.")

    else:

        screenshot = AndroidScreenshot(serial=serial)

        screenshot.capture(args.name)

    return 0


if __name__ == "__main__":

    sys.exit(main())

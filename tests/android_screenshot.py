import subprocess
from pathlib import Path


class AndroidScreenshot:

    def __init__(self):
        self.output_dir = Path("tests/images")

        self.output_dir.mkdir(
            parents=True,
            exist_ok=True
        )

    def capture(self, filename="screen.png"):

        output_path = self.output_dir / filename

        result = subprocess.run(
            [
                "adb",
                "exec-out",
                "screencap",
                "-p",
            ],
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


if __name__ == "__main__":

    screenshot = AndroidScreenshot()

    screenshot.capture(
        "eatventure.png"
    )
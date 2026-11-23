"""Inicia o painel web em segundo plano."""

import os
from pathlib import Path
import subprocess
import sys

from core.cli import configure_locale
from core.i18n import t


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    configure_locale(args)

    root = Path(__file__).resolve().parents[1]
    windows = os.name == "nt"
    venv_python = root / ".venv" / ("Scripts/python.exe" if windows else "bin/python")
    python = venv_python if venv_python.is_file() else sys.executable
    command = [str(python), "-u", str(root / "src" / "web.py"), *args]

    try:
        if "-h" in args or "--help" in args:
            return subprocess.run(command, cwd=root, check=False).returncode

        if windows:
            options = {
                "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
            }
        else:
            command.insert(0, "nohup")
            options = {"start_new_session": True}

        log_path = root / "logs" / "web.log"
        log_path.parent.mkdir(exist_ok=True)
        with log_path.open("ab") as output:
            process = subprocess.Popen(
                command, cwd=root, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT, **options,
            )
    except OSError as error:
        print(error, file=sys.stderr)
        return 1

    # Permite detectar falhas imediatas sem aguardar o encerramento do painel.
    try:
        status = process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        print(t("terminal.background_web_started", path=log_path))
        return 0
    print(t("terminal.background_web_failed", path=log_path), file=sys.stderr)
    return status if status > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

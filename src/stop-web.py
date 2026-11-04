"""Encerra o painel web iniciado por src/web.py."""

import argparse
import os
from pathlib import Path
import signal
import subprocess
import time


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def web_process(pid):
    if os.name == "nt":
        return True
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return b"web.py" in command


def remove_pid(path, pid):
    try:
        if path.read_text(encoding="ascii").strip() == str(pid):
            path.unlink(missing_ok=True)
    except OSError:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="Encerra o painel web do EatVenture")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "local",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    path = args.state_dir.resolve() / "web.pid"
    try:
        pid = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        print("Painel web não está em execução.")
        return 0
    if not alive(pid):
        remove_pid(path, pid)
        print("PID antigo removido; painel web não está em execução.")
        return 0
    if not web_process(pid):
        raise SystemExit(f"O PID {pid} não pertence ao painel web; não foi encerrado.")

    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + max(0.0, args.timeout)
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if alive(pid):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
        else:
            os.kill(pid, signal.SIGKILL)
    remove_pid(path, pid)
    print("Painel web encerrado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

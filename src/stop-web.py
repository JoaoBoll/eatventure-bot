"""Encerra o painel web iniciado por src/web.py."""

import argparse
import json
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


def _windows_command_line(pid):
    command = (
        f'$process = Get-CimInstance Win32_Process -Filter "ProcessId = {pid}" '
        '-ErrorAction Stop; $process.CommandLine | ConvertTo-Json -Compress'
    )
    try:
        result = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive",
                "-Command", command,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        value = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _windows_arguments(command_line):
    import ctypes
    from ctypes import wintypes

    count = ctypes.c_int()
    parser = ctypes.windll.shell32.CommandLineToArgvW
    parser.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
    parser.restype = ctypes.POINTER(wintypes.LPWSTR)
    arguments = parser(command_line, ctypes.byref(count))
    if not arguments:
        return []
    try:
        return [arguments[index] for index in range(count.value)]
    finally:
        release = ctypes.windll.kernel32.LocalFree
        release.argtypes = (ctypes.c_void_p,)
        release.restype = ctypes.c_void_p
        release(arguments)


def _is_web_command(arguments):
    if not arguments:
        return False
    executable = Path(arguments[0]).name.casefold()
    if not (executable.startswith("python") or executable.startswith("pypy")):
        return False
    return any(
        Path(argument).name.casefold() == "web.py"
        for argument in arguments[1:]
    )


def web_process(pid):
    if os.name == "nt":
        command_line = _windows_command_line(pid)
        if command_line is None:
            return False
        return _is_web_command(_windows_arguments(command_line))
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return _is_web_command(
        [part.decode(errors="replace") for part in command.split(b"\0") if part]
    )


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

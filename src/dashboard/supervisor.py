"""Descoberta ADB e processos isolados para o painel web."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid


_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_HOSTNAME = re.compile(r"(?=.{1,253}\Z)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\Z")
_MAX_STATUS_BYTES = 128 * 1024


def _iso_time(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _wifi(serial):
    return ":" in serial or "_adb-tls-" in serial


def _adb_run(executable, args, timeout=8):
    try:
        result = subprocess.run(
            [str(executable), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ADB não encontrado. Instale o Android Platform Tools e confira o PATH.") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Falha ao executar ADB: {exc}") from exc
    if result.returncode:
        raise RuntimeError((result.stdout or "Falha ao executar ADB.").strip()[:2000])
    return result.stdout


def _network_address(address):
    if not isinstance(address, str):
        raise ValueError("Informe o endereço no formato host:porta.")
    address = address.strip()
    if address.startswith("["):
        match = re.fullmatch(r"\[([^\]]+)\]:(\d{1,5})", address)
        if not match:
            raise ValueError("Endereço IPv6 inválido. Use [endereço]:porta.")
        host, port = match.groups()
        try:
            ipaddress.IPv6Address(host)
        except ValueError as exc:
            raise ValueError("Endereço IPv6 inválido.") from exc
    else:
        match = re.fullmatch(r"([^:]+):(\d{1,5})", address)
        if not match or not _HOSTNAME.fullmatch(match.group(1)):
            raise ValueError("Informe um IP ou nome de host seguido de :porta.")
        host, port = match.groups()
        if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
               for label in host.split(".")):
            raise ValueError("Nome de host inválido.")
    if not 1 <= int(port) <= 65535:
        raise ValueError("A porta deve estar entre 1 e 65535.")
    return address


class DeviceManager:
    def __init__(self, adb_path=None, cache_seconds=5):
        self.adb_path = adb_path
        self.cache_seconds = cache_seconds
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._devices = []
        self._properties = {}
        self._updated_at = 0.0
        self.last_error = None

    def _executable(self):
        if self.adb_path is not None:
            return self.adb_path
        from core.config import ADB_PATH

        return ADB_PATH

    def invalidate(self):
        with self._lock:
            self._updated_at = 0.0

    def _properties_for(self, serial):
        with self._lock:
            cached = self._properties.get(serial)
            if cached and time.monotonic() - cached["updated_at"] < 60:
                return cached.copy()
        values = {}
        for key, prop in (("model", "ro.product.model"), ("hardware", "ro.serialno")):
            try:
                value = _adb_run(self._executable(), ["-s", serial, "shell", "getprop", prop], timeout=2).strip()
            except RuntimeError:
                value = ""
            values[key] = value if value and value.lower() not in {"unknown", "null"} else None
        values["updated_at"] = time.monotonic()
        with self._lock:
            previous = self._properties.get(serial, {})
            for key in ("model", "hardware"):
                values[key] = values[key] or previous.get(key)
            self._properties[serial] = values
        return values.copy()

    def list(self, refresh=False):
        with self._lock:
            if not refresh and self._updated_at and time.monotonic() - self._updated_at < self.cache_seconds:
                return [dict(device, aliases=list(device["aliases"])) for device in self._devices]
        with self._refresh_lock:
            with self._lock:
                if not refresh and self._updated_at and time.monotonic() - self._updated_at < self.cache_seconds:
                    return [dict(device, aliases=list(device["aliases"])) for device in self._devices]
            try:
                output = _adb_run(self._executable(), ["devices", "-l"])
                rows = []
                for line in output.splitlines():
                    fields = line.split()
                    if len(fields) < 2 or fields[0] in {"List", "*"}:
                        continue
                    serial, state = fields[:2]
                    if state not in {"device", "offline", "unauthorized", "no"}:
                        continue
                    details = dict(field.split(":", 1) for field in fields[2:] if ":" in field)
                    rows.append({"serial": serial, "adb_state": state, "model": details.get("model", "").replace("_", " ")})
                online = [row["serial"] for row in rows if row["adb_state"] == "device"]
                with ThreadPoolExecutor(max_workers=min(8, len(online) or 1)) as pool:
                    properties = dict(zip(online, pool.map(self._properties_for, online)))
                grouped = {}
                with self._lock:
                    known = dict(self._properties)
                for row in rows:
                    serial = row["serial"]
                    info = properties.get(serial, known.get(serial, {}))
                    device_id = info.get("hardware") or serial
                    row.update({
                        "id": device_id,
                        "model": info.get("model") or row["model"] or None,
                        "connection": "wifi" if _wifi(serial) else "usb",
                        "connected": row["adb_state"] == "device",
                        "aliases": [serial],
                    })
                    previous = grouped.get(device_id)
                    if previous is None:
                        grouped[device_id] = row
                    else:
                        aliases = previous["aliases"] + [serial]
                        if (row["connected"], row["connection"] == "usb") > (previous["connected"], previous["connection"] == "usb"):
                            grouped[device_id] = row
                        grouped[device_id]["aliases"] = aliases
                devices = sorted(grouped.values(), key=lambda device: (not device["connected"], device["model"] or device["id"]))
                error = None
            except RuntimeError as exc:
                devices = []
                error = str(exc)
            with self._lock:
                self._devices = devices
                self._updated_at = time.monotonic()
                self.last_error = error
                return [dict(device, aliases=list(device["aliases"])) for device in devices]

    def connect(self, address):
        output = _adb_run(self._executable(), ["connect", _network_address(address)], timeout=12).strip()
        if not any(text in output.lower() for text in ("connected to", "already connected")):
            raise RuntimeError(output or "O ADB não confirmou a conexão.")
        self.invalidate()
        return output

    def pair(self, address, code):
        address = _network_address(address)
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code.strip()):
            raise ValueError("Informe o código de pareamento de seis dígitos.")
        output = _adb_run(self._executable(), ["pair", address, code.strip()], timeout=20).strip()
        if "successfully paired" not in output.lower():
            raise RuntimeError(output or "O ADB não confirmou o pareamento.")
        self.invalidate()
        return output


@dataclass
class _Run:
    process: object
    started_at: float
    session_id: str
    serial: str
    status_file: Path
    stop_file: Path
    lines: deque = field(default_factory=lambda: deque(maxlen=2000))
    cursor: int = 0
    stopping: bool = False
    stop_requested: bool = False
    exit_code: object = None
    reader: object = None
    watcher: object = None
    group_cleaned: bool = False


class BotSupervisor:
    def __init__(self, root, state_root=None, store=None, devices=None):
        self.root = Path(root).resolve()
        self.state_root = Path(state_root or self.root / "local").resolve()
        if store is None:
            from core.device_profiles import ProfileStore

            store = ProfileStore(self.state_root / "devices")
        self.store = store
        self.devices = devices or DeviceManager()
        self._lock = threading.RLock()
        self._operation_locks = {}
        self._runs = {}
        self._closing = False

    def _operation_lock(self, device_id):
        with self._lock:
            return self._operation_locks.setdefault(device_id, threading.RLock())

    def _catalog(self, refresh=False):
        catalog = {device["id"]: device for device in self.devices.list(refresh=refresh)}
        for profile in self.store.list_profiles():
            device_id = profile["device_id"]
            catalog.setdefault(device_id, {
                "id": device_id, "serial": None, "model": None,
                "connection": None, "adb_state": "disconnected", "aliases": [], "connected": False,
            })
        with self._lock:
            for device_id, run in self._runs.items():
                catalog.setdefault(device_id, {
                    "id": device_id, "serial": run.serial, "model": None,
                    "connection": "wifi" if _wifi(run.serial) else "usb",
                    "adb_state": "disconnected", "aliases": [run.serial], "connected": False,
                })
        return catalog

    def _resolve(self, device_id, refresh=False):
        if not isinstance(device_id, str) or not device_id or len(device_id) > 512:
            raise KeyError(device_id)
        catalog = self._catalog(refresh)
        if device_id in catalog:
            return catalog[device_id]
        for device in catalog.values():
            if device_id in device["aliases"]:
                return device
        raise KeyError(device_id)

    def _metrics(self, device_id, run):
        if run is None or run.process.poll() is not None:
            return {}
        try:
            with run.status_file.open("r", encoding="utf-8") as stream:
                raw = stream.read(_MAX_STATUS_BYTES + 1)
            if len(raw) > _MAX_STATUS_BYTES:
                return {}
            status = json.loads(raw)
            if not isinstance(status, dict):
                return {}
            updated_at = float(status.get("updated_at", 0))
            now = time.time()
            if (status.get("pid") != run.process.pid
                    or status.get("device_id") != device_id
                    or status.get("session_id") != run.session_id
                    or updated_at < run.started_at
                    or not -2 <= now - updated_at <= 15):
                return {}
            return status
        except (OSError, ValueError, TypeError):
            return {}

    def _describe(self, device):
        device = dict(device)
        device_id = device["id"]
        profile = self.store.load(device_id)
        with self._lock:
            run = self._runs.get(device_id)
            exit_code = run.process.poll() if run else None
            running = run is not None and exit_code is None
            if running:
                status = "stopping" if run.stopping else "running"
            elif run and exit_code and not run.stop_requested:
                status = "error"
            elif not device["connected"]:
                status = device["adb_state"]
            else:
                status = "stopped"
            device.update({
                "name": profile.get("name") or device.get("model") or device_id,
                "running": running,
                "pid": run.process.pid if running else None,
                "started_at": _iso_time(run.started_at) if run else None,
                "exit_code": exit_code,
                "status": status,
                "profile": profile,
            })
        device["metrics"] = self._metrics(device_id, run)
        return device

    def list_devices(self, refresh=False):
        return [self._describe(device) for device in self._catalog(refresh).values()]

    def _append_log(self, run, text):
        text = _ANSI.sub("", text).replace("\x00", "").strip("\r\n")
        if not text:
            return
        with self._lock:
            run.cursor += 1
            run.lines.append({"id": run.cursor, "time": _iso_time(time.time()), "text": text})

    def _read_output(self, run):
        try:
            if run.process.stdout is not None:
                while True:
                    line = run.process.stdout.readline(16384)
                    if not line:
                        break
                    self._append_log(run, line)
        except (OSError, ValueError) as exc:
            self._append_log(run, f"Falha lendo saída do bot: {exc}")
        finally:
            if run.process.stdout is not None:
                run.process.stdout.close()

    def _watch_process(self, run):
        run.exit_code = run.process.wait()
        self._cleanup_group(run)

    def _cleanup_group(self, run):
        if os.name != "nt":
            with self._lock:
                if run.group_cleaned:
                    return
                run.group_cleaned = True
            # O treino pode sobreviver a uma falha do worker.
            self._signal(run, signal.SIGKILL)

    def start(self, device_id):
        device = self._resolve(device_id)
        device_id = device["id"]
        with self._operation_lock(device_id):
            with self._lock:
                if self._closing:
                    raise RuntimeError("O painel está sendo encerrado.")
                previous = self._runs.get(device_id)
                if previous is not None and previous.process.poll() is None:
                    return self._describe(device)
                for running_id, active in self._runs.items():
                    if running_id != device_id and active.serial in device["aliases"] and active.process.poll() is None:
                        raise RuntimeError("Este aparelho já está em execução por outra conexão ADB.")
            if not device["connected"]:
                raise RuntimeError("O dispositivo não está autorizado e conectado ao ADB.")
            profile = self.store.load(device_id)
            profile_path = Path(self.store.path_for(device_id))
            if not profile_path.is_file():
                profile = self.store.save(device_id, profile)
            runtime = self.state_root / "runtime"
            runtime.mkdir(parents=True, exist_ok=True)
            key = hashlib.sha256(device_id.encode("utf-8")).hexdigest()
            status_file = runtime / f"{key}.status.json"
            stop_file = runtime / f"{key}.stop"
            for path in (status_file, stop_file):
                path.unlink(missing_ok=True)
            session_id = uuid.uuid4().hex
            environment = os.environ.copy()
            environment.update({
                "PYTHONUNBUFFERED": "1",
                "EATVENTURE_PROFILE": str(profile_path.resolve()),
                "EATVENTURE_DEVICE_ID": device_id,
                "EATVENTURE_DEVICE_SERIAL": device["serial"],
                "EATVENTURE_STATE_ROOT": str(self.state_root),
                "EATVENTURE_STATUS_FILE": str(status_file),
                "EATVENTURE_STOP_FILE": str(stop_file),
                "EATVENTURE_SESSION_ID": session_id,
            })
            command = [sys.executable, "-u", str(self.root / "src" / "web_worker.py"), "--device", device["serial"]]
            options = profile.get("run", {})
            for key, argument in (("ai_collect", "--ai-collect"), ("layout_only", "--layout-only"), ("save_log", "--log")):
                if options.get(key):
                    command.append(argument)
            process_options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
            started_at = time.time()
            with self._lock:
                if self._closing:
                    raise RuntimeError("O painel está sendo encerrado.")
                for running_id, active in self._runs.items():
                    if running_id != device_id and active.serial in device["aliases"] and active.process.poll() is None:
                        raise RuntimeError("Este aparelho já está em execução por outra conexão ADB.")
                try:
                    process = subprocess.Popen(
                        command,
                        cwd=str(self.root),
                        env=environment,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                        **process_options,
                    )
                except OSError as exc:
                    raise RuntimeError(f"Não foi possível iniciar o bot: {exc}") from exc
                run = _Run(process, started_at, session_id, device["serial"], status_file, stop_file)
                if previous:
                    run.lines.extend(previous.lines)
                    run.cursor = previous.cursor
                self._runs[device_id] = run
            self._append_log(run, f"Bot iniciado (PID {process.pid}).")
            run.reader = threading.Thread(target=self._read_output, args=(run,), name=f"bot-log-{process.pid}", daemon=True)
            run.watcher = threading.Thread(target=self._watch_process, args=(run,), name=f"bot-wait-{process.pid}", daemon=True)
            run.reader.start()
            run.watcher.start()
            return self._describe(device)

    def _signal(self, run, value):
        try:
            if os.name == "nt":
                if run.process.poll() is None:
                    run.process.send_signal(value)
            else:
                os.killpg(run.process.pid, value)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _force_stop(self, run):
        if os.name == "nt":
            if run.process.poll() is None:
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(run.process.pid), "/T", "/F"],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=5, check=False,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    run.process.kill()
        else:
            self._signal(run, signal.SIGKILL)
        try:
            run.process.wait(timeout=3)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Não foi possível encerrar o processo do bot.") from exc

    def stop(self, device_id):
        device = self._resolve(device_id)
        device_id = device["id"]
        with self._operation_lock(device_id):
            with self._lock:
                run = self._runs.get(device_id)
                if run is None or run.process.poll() is not None:
                    if run is not None:
                        self._cleanup_group(run)
                    return self._describe(device)
                run.stopping = True
                run.stop_requested = True
            self._append_log(run, "Encerramento solicitado pelo painel.")
            try:
                run.stop_file.write_text(run.session_id, encoding="utf-8")
            except OSError:
                self._signal(run, signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
            try:
                run.process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                self._signal(run, signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
                try:
                    run.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    if os.name != "nt":
                        self._signal(run, signal.SIGTERM)
                        try:
                            run.process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            pass
                    self._force_stop(run)
            self._cleanup_group(run)
            run.exit_code = run.process.poll()
            run.stopping = False
            if run.reader is not None:
                run.reader.join(timeout=1)
            self._append_log(run, f"Bot encerrado (código {run.exit_code}).")
            return self._describe(device)

    def restart(self, device_id):
        device = self._resolve(device_id)
        with self._operation_lock(device["id"]):
            self.stop(device["id"])
            return self.start(device["id"])

    def logs(self, device_id, after=0, limit=200):
        device = self._resolve(device_id)
        try:
            after = max(0, int(after))
            limit = min(1000, max(1, int(limit)))
        except (ValueError, TypeError) as exc:
            raise ValueError("Cursor e limite dos logs devem ser números inteiros.") from exc
        with self._lock:
            run = self._runs.get(device["id"])
            lines = [dict(line) for line in run.lines if line["id"] > after][:limit] if run else []
        return {"lines": lines, "cursor": lines[-1]["id"] if lines else after}

    def connect(self, address):
        message = self.devices.connect(address)
        return {"message": message, "devices": self.list_devices(refresh=True)}

    def pair(self, address, code):
        message = self.devices.pair(address, code)
        return {"message": message, "devices": self.list_devices(refresh=True)}

    def shutdown(self):
        with self._lock:
            self._closing = True
            device_ids = [device_id for device_id, run in self._runs.items() if run.process.poll() is None]
        if device_ids:
            with ThreadPoolExecutor(max_workers=min(8, len(device_ids))) as pool:
                futures = [pool.submit(self.stop, device_id) for device_id in device_ids]
                errors = []
                for future in futures:
                    try:
                        future.result()
                    except (OSError, RuntimeError) as exc:
                        errors.append(str(exc))
                if errors:
                    raise RuntimeError("; ".join(errors))

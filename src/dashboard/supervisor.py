"""Descoberta ADB e processos isolados para o painel web."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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
import tempfile
import threading
import time
import uuid

from core import log
from core.preview import FramePreview, preview_paths


logger = log.get("dashboard.supervisor")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_HOSTNAME = re.compile(r"(?=.{1,253}\Z)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\Z")
_MAX_STATUS_BYTES = 128 * 1024
MAX_LOG_LINES = 500
MAX_LOG_BYTES = 1024 * 1024
MAX_LOG_LINE_BYTES = 4096


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
        self._network_ids = {}
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
        for key, props in (("model", ("ro.product.model",)), ("hardware", ("ro.serialno", "ro.boot.serialno"))):
            value = None
            for prop in props:
                try:
                    candidate = _adb_run(self._executable(), ["-s", serial, "shell", "getprop", prop], timeout=2).strip()
                except RuntimeError:
                    continue
                if candidate and candidate.lower() not in {"unknown", "null"}:
                    value = candidate
                    break
            values[key] = value
        values["updated_at"] = time.monotonic()
        with self._lock:
            previous = self._properties.get(serial, {})
            for key in ("model", "hardware"):
                values[key] = values[key] or previous.get(key)
            self._properties[serial] = values
        return values.copy()

    @staticmethod
    def _parse_devices(output):
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
        return rows

    @staticmethod
    def _mdns_aliases(service):
        return {
            service["address"], service["instance"],
            f"{service['instance']}.{service['type']}",
        }

    @staticmethod
    def _network_host(serial):
        if serial.startswith("["):
            closing = serial.find("]:")
            return serial[1:closing] if closing > 0 and serial[closing + 2:].isdigit() else None
        if ":" in serial:
            host, port = serial.rsplit(":", 1)
            if host and port.isdigit():
                return host
        return None

    def _mdns_services(self):
        try:
            output = _adb_run(self._executable(), ["mdns", "services"], timeout=5)
        except RuntimeError:
            return []
        services = []
        for line in output.splitlines():
            fields = line.split()
            if len(fields) < 3 or fields[1] != "_adb-tls-connect._tcp":
                continue
            try:
                address = _network_address(fields[2])
            except ValueError:
                continue
            instance = fields[0].removesuffix(f".{fields[1]}")
            if instance:
                services.append({"instance": instance, "type": fields[1], "address": address})
        return services

    def _connect_mdns_service(self, service):
        try:
            _adb_run(self._executable(), ["connect", service["address"]], timeout=8)
            return True
        except RuntimeError:
            return False

    def list(self, refresh=False):
        with self._lock:
            if not refresh and self._updated_at and time.monotonic() - self._updated_at < self.cache_seconds:
                return [dict(device, aliases=list(device["aliases"])) for device in self._devices]
        with self._refresh_lock:
            with self._lock:
                if not refresh and self._updated_at and time.monotonic() - self._updated_at < self.cache_seconds:
                    return [dict(device, aliases=list(device["aliases"])) for device in self._devices]
            try:
                services = self._mdns_services()
                rows = self._parse_devices(_adb_run(self._executable(), ["devices", "-l"]))
                online = {row["serial"] for row in rows if row["adb_state"] == "device"}
                missing_services = [
                    service for service in services
                    if not online.intersection(self._mdns_aliases(service))
                    and not any(
                        _wifi(serial)
                        and serial.rsplit(":", 1)[0] == service["address"].rsplit(":", 1)[0]
                        for serial in online
                    )
                ]
                with ThreadPoolExecutor(max_workers=min(8, len(missing_services) or 1)) as pool:
                    reconnecting = any(pool.map(self._connect_mdns_service, missing_services))
                if reconnecting:
                    rows = self._parse_devices(_adb_run(self._executable(), ["devices", "-l"]))
                online = [row["serial"] for row in rows if row["adb_state"] == "device"]
                with ThreadPoolExecutor(max_workers=min(8, len(online) or 1)) as pool:
                    properties = dict(zip(online, pool.map(self._properties_for, online)))
                grouped = {}
                with self._lock:
                    known = dict(self._properties)
                mdns_by_alias = {
                    alias: service
                    for service in services
                    for alias in self._mdns_aliases(service)
                }
                network_ids = dict(self._network_ids)
                for serial in online:
                    host = self._network_host(serial)
                    if not host:
                        continue
                    hardware = properties.get(serial, {}).get("hardware")
                    service = mdns_by_alias.get(serial)
                    if hardware:
                        network_ids[host] = hardware
                    elif service is not None:
                        network_ids.setdefault(host, service["instance"])
                    else:
                        network_ids.setdefault(host, f"wifi:{host}")
                for row in rows:
                    serial = row["serial"]
                    info = properties.get(serial, known.get(serial, {}))
                    service = mdns_by_alias.get(serial)
                    host = self._network_host(serial) if _wifi(serial) else None
                    if info.get("hardware"):
                        device_id = info["hardware"]
                    elif host:
                        device_id = network_ids[host]
                    elif service is not None:
                        device_id = service["instance"]
                    else:
                        device_id = serial
                    aliases = [serial]
                    if service is not None:
                        aliases.extend(self._mdns_aliases(service))
                    row.update({
                        "id": device_id,
                        "model": info.get("model") or row["model"] or None,
                        "connection": "wifi" if _wifi(serial) else "usb",
                        "connected": row["adb_state"] == "device",
                        "aliases": list(dict.fromkeys(aliases)),
                    })
                    previous = grouped.get(device_id)
                    if previous is None:
                        grouped[device_id] = row
                    else:
                        aliases = list(dict.fromkeys(previous["aliases"] + row["aliases"]))
                        if (
                            row["connected"],
                            row["connection"] == "usb",
                            row["serial"].endswith(":5555"),
                        ) > (
                            previous["connected"],
                            previous["connection"] == "usb",
                            previous["serial"].endswith(":5555"),
                        ):
                            grouped[device_id] = row
                        grouped[device_id]["aliases"] = aliases
                devices = sorted(grouped.values(), key=lambda device: (not device["connected"], device["model"] or device["id"]))
                error = None
            except RuntimeError as exc:
                devices = []
                error = str(exc)
            with self._lock:
                self._network_ids = network_ids if error is None else self._network_ids
                self._devices = devices
                self._updated_at = time.monotonic()
                self.last_error = error
                return [dict(device, aliases=list(device["aliases"])) for device in devices]

    def connect(self, address):
        message, _ = self.connect_with_address(address)
        return message

    def connect_with_address(self, address):
        address = _network_address(address)
        output = _adb_run(self._executable(), ["connect", address], timeout=12).strip()
        if not any(text in output.lower() for text in ("connected to", "already connected")):
            raise RuntimeError(output or "O ADB não confirmou a conexão.")
        target_address = address
        if address.rsplit(":", 1)[1] != "5555":
            try:
                tcpip_output = _adb_run(
                    self._executable(), ["-s", address, "tcpip", "5555"], timeout=12
                ).strip()
            except RuntimeError as error:
                output = (
                    f"{output}\nNão foi possível aplicar a porta 5555; "
                    f"a conexão continua em {address}. {error}"
                )
            else:
                target_address = f"{address.rsplit(':', 1)[0]}:5555"
                reconnect_output = ""
                reconnect_error = None
                for attempt in range(5):
                    try:
                        reconnect_output = _adb_run(
                            self._executable(), ["connect", target_address], timeout=8
                        ).strip()
                        reconnect_error = None
                    except RuntimeError as error:
                        reconnect_output = ""
                        reconnect_error = str(error)
                    if any(
                        text in reconnect_output.lower()
                        for text in ("connected to", "already connected")
                    ):
                        break
                    if attempt < 4:
                        time.sleep(1)
                else:
                    detail = reconnect_output or reconnect_error or tcpip_output
                    raise RuntimeError(
                        f"O ADB aplicou a porta 5555, mas não reconectou em "
                        f"{target_address}. {detail}"
                    )
                output = f"{output}\n{tcpip_output}\n{reconnect_output}".strip()
        self.invalidate()
        return output, target_address

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
    lines: deque = field(default_factory=deque)
    cursor: int = 0
    log_bytes: int = 0
    log_dropped: int = 0
    log_truncated: int = 0
    stopping: bool = False
    stop_requested: bool = False
    exit_code: object = None
    reader: object = None
    watcher: object = None
    group_cleaned: bool = False
    worker_pid: object = None


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
        self._auto_training_lock = threading.RLock()
        self._operation_locks = {}
        self._runs = {}
        self._device_cache = {}
        self._auto_trainer = None
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
            self._device_cache = {key: dict(device) for key, device in catalog.items()}
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

    def _runtime_device_id(self, device_id):
        with self._lock:
            if device_id in self._runs:
                return device_id
            for identifier, run in self._runs.items():
                aliases = self._device_cache.get(identifier, {}).get("aliases", [])
                if device_id == run.serial or device_id in aliases:
                    return identifier
        return self._resolve(device_id)["id"]

    def require_device(self, device_id):
        if not isinstance(device_id, str) or not device_id or len(device_id) > 512:
            raise KeyError(device_id)
        with self._lock:
            if device_id in self._runs or device_id in self._device_cache:
                return
        self._resolve(device_id)

    def _ensure_auto_trainer(self):
        from core import config

        with self._lock:
            if self._closing or self._auto_trainer is not None:
                return
            enabled = False
            for device_id, run in self._runs.items():
                if run.process.poll() is not None:
                    continue
                profile = self.store.load(device_id)
                if (
                    profile["run"]["ai_collect"]
                    and profile["settings"].get("ai_auto_train", config.AI_AUTO_TRAIN)
                ):
                    enabled = True
                    break
            if not enabled:
                return
            from dataset.auto_train import AutoTrainer

            trainer = AutoTrainer(
                config.DATASET_DIR,
                batch_size=config.AI_TRAIN_BATCH_SIZE,
                interval=config.AI_TRAIN_CHECK_INTERVAL,
                jobs=config.AI_TRAIN_JOBS,
                pause_bot=False,
                training_session=self._training_session,
            )
            self._auto_trainer = trainer
        trainer.start()

    @contextmanager
    def _training_session(self, resolution):
        with self._auto_training_lock:
            with self._lock:
                device_ids = [
                    device_id for device_id, run in self._runs.items()
                    if run.process.poll() is None
                    and self.store.load(device_id)["run"]["ai_collect"]
                ]
            restart_ids = []
            for device_id in device_ids:
                try:
                    self.stop(device_id)
                    restart_ids.append(device_id)
                except (KeyError, RuntimeError, OSError):
                    logger.exception(
                        "Não foi possível parar o bot %s antes do treino da resolução %s.",
                        device_id, resolution,
                    )
                    for stopped_id in restart_ids:
                        try:
                            self.start(stopped_id)
                        except (KeyError, RuntimeError, OSError):
                            logger.exception("Não foi possível retomar o bot %s.", stopped_id)
                    raise
            try:
                yield
            finally:
                if not self._closing:
                    for device_id in restart_ids:
                        try:
                            self.start(device_id)
                        except (KeyError, RuntimeError, OSError):
                            logger.exception("Não foi possível retomar o bot %s após o treino.", device_id)

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
            worker_pid = status.get("pid")
            if (type(worker_pid) is not int or worker_pid <= 0
                    or status.get("device_id") != device_id
                    or status.get("session_id") != run.session_id
                    or updated_at < run.started_at
                    or not -2 <= now - updated_at <= 15):
                return {}
            with self._lock:
                # O launcher da venv no Windows pode ter outro PID; a sessão identifica o worker.
                if run.worker_pid is None:
                    run.worker_pid = worker_pid
                if run.worker_pid != worker_pid:
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

    def runtime_state(self):
        with self._lock:
            catalog = {key: dict(device) for key, device in self._device_cache.items()}
            for device_id, run in self._runs.items():
                catalog.setdefault(device_id, {
                    "id": device_id, "serial": run.serial, "model": None,
                    "connection": "wifi" if _wifi(run.serial) else "usb",
                    "adb_state": "device" if run.process.poll() is None else "disconnected",
                    "aliases": [run.serial], "connected": run.process.poll() is None,
                })
        return {
            "devices": [self._describe(device) for device in catalog.values()],
            "error": self.devices.last_error,
        }

    def _append_log(self, run, text):
        text = _ANSI.sub("", text).replace("\x00", "").strip("\r\n")
        if not text:
            return
        encoded = text.encode("utf-8", errors="replace")
        truncated = len(encoded) > MAX_LOG_LINE_BYTES
        if truncated:
            text = encoded[:MAX_LOG_LINE_BYTES - 3].decode("utf-8", errors="ignore") + "…"
            encoded = text.encode("utf-8")
        else:
            text = encoded.decode("utf-8")
        with self._lock:
            run.cursor += 1
            run.lines.append({"id": run.cursor, "time": _iso_time(time.time()), "text": text})
            run.log_bytes += len(encoded)
            run.log_truncated += int(truncated)
            self._trim_logs(run)

    @staticmethod
    def _trim_logs(run):
        while run.lines and (len(run.lines) > MAX_LOG_LINES or run.log_bytes > MAX_LOG_BYTES):
            removed = run.lines.popleft()
            run.log_bytes -= len(removed["text"].encode("utf-8"))
            run.log_dropped += 1

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
        FramePreview(run.status_file, run.session_id).clear()
        if os.name != "nt":
            with self._lock:
                if run.group_cleaned:
                    return
                run.group_cleaned = True
            # O treino pode sobreviver a uma falha do worker.
            self._signal(run, signal.SIGKILL)

    def start(self, device_id):
        with self._auto_training_lock:
            return self._start(device_id)

    def _start(self, device_id):
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
                "EATVENTURE_AUTO_TRAIN_EXTERNAL": "1",
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
                    run.log_bytes = previous.log_bytes
                    run.log_dropped = previous.log_dropped
                    run.log_truncated = previous.log_truncated
                    self._trim_logs(run)
                self._runs[device_id] = run
            self._append_log(run, f"Bot iniciado (PID {process.pid}).")
            run.reader = threading.Thread(target=self._read_output, args=(run,), name=f"bot-log-{process.pid}", daemon=True)
            run.watcher = threading.Thread(target=self._watch_process, args=(run,), name=f"bot-wait-{process.pid}", daemon=True)
            run.reader.start()
            run.watcher.start()
            self._ensure_auto_trainer()
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
        with self._auto_training_lock:
            return self._stop(device_id)

    def _stop(self, device_id):
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
        with self._auto_training_lock:
            device = self._resolve(device_id)
            with self._operation_lock(device["id"]):
                self.stop(device["id"])
                return self.start(device["id"])

    def request_vision(self, device_id, enabled):
        device_id = self._runtime_device_id(device_id)
        with self._operation_lock(device_id):
            with self._lock:
                run = self._runs.get(device_id)
                if run is None or run.process.poll() is not None:
                    if enabled:
                        raise RuntimeError("Inicie o bot para abrir a visão IA.")
                    if run is not None:
                        FramePreview(run.status_file, run.session_id).clear()
                    return {"enabled": False}
            path = preview_paths(run.status_file)["request"]
            request = {"session_id": run.session_id, "expires_at": time.time() + 8 if enabled else 0}
            temporary = None
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".vision-request-", delete=False) as output:
                    temporary = Path(output.name)
                    json.dump(request, output, allow_nan=False)
                os.replace(temporary, path)
            except OSError as error:
                raise RuntimeError("Não foi possível alterar a visualização IA.") from error
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            if not enabled:
                FramePreview(run.status_file, run.session_id).clear()
            return {"enabled": enabled}

    def vision_frame(self, device_id):
        device_id = self._runtime_device_id(device_id)
        self.request_vision(device_id, True)
        with self._lock:
            run = self._runs[device_id]
        self._metrics(device_id, run)
        paths = preview_paths(run.status_file)
        try:
            with paths["metadata"].open("r", encoding="utf-8") as stream:
                metadata = json.loads(stream.read(4097))
            updated_at = float(metadata.get("updated_at", 0))
            stat = paths["image"].stat()
            if (run.process.poll() is not None
                    or metadata.get("session_id") != run.session_id
                    or metadata.get("pid") != (run.worker_pid or run.process.pid)
                    or updated_at < run.started_at
                    or not -2 <= time.time() - updated_at <= 5
                    or not 0 < stat.st_size <= 5_000_000):
                raise ValueError("Prévia indisponível.")
            return paths["image"]
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise RuntimeError("Aguardando frame da visão IA.") from error

    def open_vision_stream(self, device_id):
        if not isinstance(device_id, str) or not device_id or len(device_id) > 512:
            raise KeyError(device_id)
        with self._lock:
            identifier = device_id if device_id in self._runs else None
            if identifier is None:
                for candidate, run in self._runs.items():
                    aliases = self._device_cache.get(candidate, {}).get("aliases", [])
                    if device_id == run.serial or device_id in aliases:
                        identifier = candidate
                        break
            known = device_id in self._device_cache or any(
                device_id in device.get("aliases", []) for device in self._device_cache.values()
            )
        if identifier is None:
            if known or self.store.path_for(device_id).is_file():
                raise RuntimeError("Inicie o bot para abrir a visão IA.")
            raise KeyError(device_id)
        with self._operation_lock(identifier):
            with self._lock:
                run = self._runs.get(identifier)
                if run is None or run.process.poll() is not None:
                    raise RuntimeError("Inicie o bot para abrir a visão IA.")
                session_id = run.session_id
            self.request_vision(identifier, True)
            return {"device_id": identifier, "session_id": session_id}

    def next_vision_frame(self, device_id, session_id, after=None, renew=False):
        def current_run():
            with self._lock:
                run = self._runs.get(device_id)
                if run is None or run.session_id != session_id or run.process.poll() is not None:
                    raise RuntimeError("A sessão da visão IA foi encerrada.")
                return run

        run = current_run()
        if renew:
            with self._operation_lock(device_id):
                run = current_run()
                self.request_vision(device_id, True)
        if run.worker_pid is None:
            self._metrics(device_id, run)
        paths = preview_paths(run.status_file)
        try:
            with paths["metadata"].open("r", encoding="utf-8") as source:
                raw = source.read(4097)
            if len(raw) > 4096:
                return None
            metadata = json.loads(raw)
            updated_at = float(metadata.get("updated_at", 0))
            worker_pid = run.worker_pid or run.process.pid
            if (metadata.get("session_id") != session_id
                    or metadata.get("pid") != worker_pid
                    or updated_at < run.started_at
                    or not -2 <= time.time() - updated_at <= 5):
                return None
            with paths["image"].open("rb") as image:
                stat = os.fstat(image.fileno())
                if not 0 < stat.st_size <= 5_000_000:
                    return None
                signature = (session_id, worker_pid, updated_at, stat.st_mtime_ns, stat.st_size)
                if after and signature[:2] == after[:2] and signature[3:] == after[3:]:
                    return None
                content = image.read(5_000_001)
            if len(content) != stat.st_size:
                return None
        except (OSError, ValueError, TypeError, AttributeError):
            return None
        if current_run() is not run:
            raise RuntimeError("A sessão da visão IA foi encerrada.")
        return {"signature": signature, "content": content}

    def logs(self, device_id, after=0, limit=200):
        device_id = self._runtime_device_id(device_id)
        try:
            after = max(0, int(after))
            limit = min(1000, max(1, int(limit)))
        except (ValueError, TypeError) as exc:
            raise ValueError("Cursor e limite dos logs devem ser números inteiros.") from exc
        with self._lock:
            run = self._runs.get(device_id)
            lines = [dict(line) for line in run.lines if line["id"] > after][:limit] if run else []
            retention = {
                "max_lines": MAX_LOG_LINES, "max_bytes": MAX_LOG_BYTES,
                "max_line_bytes": MAX_LOG_LINE_BYTES,
                "buffer_lines": len(run.lines) if run else 0,
                "buffer_bytes": run.log_bytes if run else 0,
                "dropped": run.log_dropped if run else 0,
                "truncated": run.log_truncated if run else 0,
            }
        return {"lines": lines, "cursor": lines[-1]["id"] if lines else after, "retention": retention}

    def connect(self, address):
        message, address = self.devices.connect_with_address(address)
        return {
            "message": message,
            "address": address,
            "devices": self.list_devices(refresh=True),
        }

    def pair(self, address, code):
        message = self.devices.pair(address, code)
        return {"message": message, "devices": self.list_devices(refresh=True)}

    def shutdown(self):
        with self._lock:
            self._closing = True
            auto_trainer = self._auto_trainer
        if auto_trainer is not None:
            auto_trainer.stop()
        with self._lock:
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

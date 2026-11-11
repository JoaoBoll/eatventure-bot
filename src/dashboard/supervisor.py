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

from core import config, log
from core.i18n import SUPPORTED_LOCALES, Text, localized, t
from core.block_regions import migrate_legacy_blocks
from core.preview import FramePreview, preview_paths


logger = log.get("dashboard.supervisor")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_HOSTNAME = re.compile(r"(?=.{1,253}\Z)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\Z")
_MAX_STATUS_BYTES = 128 * 1024
_MAX_REPORT_BYTES = 4096
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
        raise RuntimeError(t("messages.adb_not_found")) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(t("messages.adb_execution_failed", exc=exc)) from exc
    if result.returncode:
        raise RuntimeError((result.stdout or "Falha ao executar ADB.").strip()[:2000])
    return result.stdout


def _network_address(address):
    if not isinstance(address, str):
        raise ValueError(t("messages.address_format_required"))
    address = address.strip()
    if address.startswith("["):
        match = re.fullmatch(r"\[([^\]]+)\]:(\d{1,5})", address)
        if not match:
            raise ValueError(t("messages.ipv6_format_required"))
        host, port = match.groups()
        try:
            ipaddress.IPv6Address(host)
        except ValueError as exc:
            raise ValueError(t("messages.invalid_ipv6")) from exc
    else:
        match = re.fullmatch(r"([^:]+):(\d{1,5})", address)
        if not match or not _HOSTNAME.fullmatch(match.group(1)):
            raise ValueError(t("messages.host_port_required"))
        host, port = match.groups()
        if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
               for label in host.split(".")):
            raise ValueError(t("messages.invalid_hostname"))
    if not 1 <= int(port) <= 65535:
        raise ValueError(t("messages.invalid_port"))
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
                hardware_ids = {
                    info.get("hardware")
                    for info in (*properties.values(), *known.values())
                    if info.get("hardware")
                }

                def service_hardware(service):
                    if service is None:
                        return None
                    instance = service["instance"]
                    return next((
                        hardware for hardware in hardware_ids
                        if instance.startswith(f"adb-{hardware}-")
                    ), None)

                network_ids = dict(self._network_ids)
                for serial in online:
                    host = self._network_host(serial)
                    if not host:
                        continue
                    service = mdns_by_alias.get(serial)
                    hardware = properties.get(serial, {}).get("hardware") or service_hardware(service)
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
                    hardware = info.get("hardware") or service_hardware(service)
                    if hardware:
                        device_id = hardware
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
                error = exc.args[0] if len(exc.args) == 1 and isinstance(exc.args[0], Text) else str(exc)
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
            raise RuntimeError(output or t("messages.adb_connection_unconfirmed"))
        target_address = address
        if address.rsplit(":", 1)[1] != "5555":
            try:
                tcpip_output = _adb_run(
                    self._executable(), ["-s", address, "tcpip", "5555"], timeout=12
                ).strip()
            except RuntimeError as error:
                output = (
                    t("messages.adb_port_switch_failed", output=output, address=address, error=error)
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
                        t("messages.adb_reconnect_failed", target_address=target_address, detail=detail)
                    )
                output = f"{output}\n{tcpip_output}\n{reconnect_output}".strip()
        self.invalidate()
        return output, target_address

    def pair(self, address, code):
        address = _network_address(address)
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code.strip()):
            raise ValueError(t("messages.pairing_code_required"))
        output = _adb_run(self._executable(), ["pair", address, code.strip()], timeout=20).strip()
        if "successfully paired" not in output.lower():
            raise RuntimeError(output or t("messages.adb_pairing_unconfirmed"))
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
    pause_file: Path | None = None
    lines: deque = field(default_factory=deque)
    cursor: int = 0
    log_bytes: int = 0
    log_dropped: int = 0
    log_truncated: int = 0
    stopping: bool = False
    stop_requested: bool = False
    restart_attempt: int = 0
    detector_mode: str = "templates"
    recovery_cancel: object = field(default_factory=threading.Event)
    exit_code: object = None
    reader: object = None
    watcher: object = None
    group_cleaned: bool = False
    worker_pid: object = None
    template_snapshots: dict = field(default_factory=dict)
    report_base: dict = field(default_factory=dict)
    dataset_base: dict = field(default_factory=dict)


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
        self._reports = {}
        self._device_cache = {}
        self._auto_trainer = None
        self._closing = False
        from dashboard.training import TrainingManager

        self.training = TrainingManager(self.root, self.state_root, training_session=self._training_session)
        self._auto_trainer = self.training

    def _operation_lock(self, device_id):
        with self._lock:
            return self._operation_locks.setdefault(device_id, threading.RLock())

    def _report_path(self, device_id):
        key = hashlib.sha256(device_id.encode("utf-8")).hexdigest()
        return self.state_root / "reports" / f"{key}.json"

    def _report(self, device_id):
        report = self._reports.get(device_id)
        if report is not None:
            return report
        report = {"voos": 0, "reformas": 0}
        path = self._report_path(device_id)
        try:
            if path.stat().st_size <= _MAX_REPORT_BYTES:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("device_id") == device_id:
                    for key in report:
                        value = data.get(key, 0)
                        if type(value) is int and value >= 0:
                            report[key] = value
        except (OSError, ValueError, UnicodeError):
            pass
        self._reports[device_id] = report
        return report

    def _save_report(self, device_id, report):
        path = self._report_path(device_id)
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False,
            ) as output:
                temporary = Path(output.name)
                json.dump(
                    {"device_id": device_id, **report}, output,
                    ensure_ascii=False, allow_nan=False,
                )
            os.replace(temporary, path)
        except OSError:
            logger.warning(t("messages.report_save_failed"), device_id)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _counter(metrics, key):
        value = metrics.get(key, 0)
        return value if type(value) is int and value >= 0 else 0

    def _sample_count(self, resolution):
        if not re.fullmatch(r"[1-9]\d*x[1-9]\d*", resolution):
            return 0
        path = self.root / "dataset" / "data" / resolution / "samples.jsonl"
        try:
            with path.open(encoding="utf-8") as source:
                return sum(1 for line in source if line.strip())
        except (OSError, UnicodeError):
            return 0

    def _sample_catalog(self):
        data_dir = self.root / "dataset" / "data"
        try:
            resolutions = [
                path.name for path in data_dir.iterdir()
                if path.is_dir() and re.fullmatch(r"[1-9]\d*x[1-9]\d*", path.name)
            ]
        except OSError:
            return {}
        return {resolution: self._sample_count(resolution) for resolution in resolutions}

    def _add_report_metrics(self, device_id, run, metrics):
        report = self._report(device_id)
        if run is None:
            metrics.update({
                "voos_total": report["voos"], "voos_delta": 0,
                "reformas_total": report["reformas"], "reformas_delta": 0,
            })
            return
        changed = False
        for key in ("voos", "reformas"):
            session_total = self._counter(metrics, key)
            total = run.report_base.get(key, report[key]) + session_total
            if total > report[key]:
                report[key] = total
                changed = True
            metrics[f"{key}_total"] = report[key]
            metrics[f"{key}_delta"] = session_total
        if changed:
            self._save_report(device_id, report)

    def _add_dataset_metrics(self, run, metrics):
        summary = metrics.get("summary")
        resolution = summary.get("resolution") if isinstance(summary, dict) else None
        if not isinstance(resolution, str) or not re.fullmatch(r"[1-9]\d*x[1-9]\d*", resolution):
            return
        session_total = self._counter(metrics, "dados_sessao")
        # `dataset_base` é o snapshot global feito antes de iniciar o worker.
        # Depois disso, o card soma apenas o contador em memória desta sessão.
        metrics["dados_coletados"] = run.dataset_base.get(resolution, 0) + session_total

    def _catalog(self, refresh=False):
        catalog = {device["id"]: device for device in self.devices.list(refresh=refresh)}

        def adopt_identity(identifier, serial=None):
            def identifies(device):
                aliases = device.get("aliases", ())
                return (
                    identifier in aliases
                    or any(alias.startswith(f"adb-{identifier}-") for alias in aliases)
                    or serial is not None and (
                        serial == device.get("serial") or serial in aliases
                    )
                )

            match = next((
                key for key, device in catalog.items()
                if key != identifier and identifies(device)
            ), None)
            if match is None:
                return
            discovered = catalog.pop(match)
            previous = catalog.get(identifier, {})
            aliases = list(dict.fromkeys([
                *previous.get("aliases", ()), *discovered.get("aliases", ()), match,
            ]))
            catalog[identifier] = {**previous, **discovered, "id": identifier, "aliases": aliases}

        for profile in self.store.list_profiles():
            device_id = profile["device_id"]
            adopt_identity(device_id)
            catalog.setdefault(device_id, {
                "id": device_id, "serial": None, "model": None,
                "connection": None, "adb_state": "disconnected", "aliases": [], "connected": False,
            })
        with self._lock:
            for device_id, run in self._runs.items():
                adopt_identity(device_id, run.serial)
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
        with self._lock:
            if self._closing:
                return
            self.training.start()

    @contextmanager
    def _training_session(self, resolution, pause=True):
        with self._auto_training_lock:
            if not pause:
                yield
                return
            with self._lock:
                active_runs = [
                    (device_id, run) for device_id, run in self._runs.items()
                    if run.process.poll() is None
                ]
            # O snapshot do dataset e a limpeza por linha já preservam dados
            # escritos depois do início do treino. Só pausamos quem coleta a
            # mesma resolução para reduzir disputa de CPU e I/O, sem derrubar
            # aparelhos que treinam outro shard.
            device_ids = []
            for device_id, run in active_runs:
                summary = self._metrics(device_id, run).get("summary", {})
                if summary.get("resolution") == resolution:
                    device_ids.append(device_id)
            restart_ids = []
            for device_id in device_ids:
                try:
                    self.stop(device_id)
                    restart_ids.append(device_id)
                except (KeyError, RuntimeError, OSError):
                    logger.exception(
                        t("messages.training_stop_bot_failed"),
                        device_id, resolution,
                    )
                    for stopped_id in restart_ids:
                        try:
                            self.start(stopped_id)
                        except (KeyError, RuntimeError, OSError):
                            logger.exception(t("messages.bot_resume_failed"), stopped_id)
                    raise
            try:
                yield
            finally:
                if not self._closing:
                    for device_id in restart_ids:
                        try:
                            self.start(device_id)
                        except (KeyError, RuntimeError, OSError):
                            logger.exception(t("messages.post_training_resume_failed"), device_id)

    def _metrics(self, device_id, run, allow_finished=False):
        if run is None or (not allow_finished and run.process.poll() is not None):
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
        profile = migrate_legacy_blocks(self.store, device_id, device.get("aliases", []))
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
                "session_id": run.session_id if run else None,
                "pid": run.process.pid if running else None,
                "started_at": _iso_time(run.started_at) if run else None,
                "exit_code": exit_code,
                "status": status,
                "profile": profile,
                "restart_attempt": run.restart_attempt if run else 0,
                "detector_mode": run.detector_mode if running else profile["run"]["detector_mode"],
            })
        metrics = self._metrics(device_id, run)
        with self._lock:
            self._add_report_metrics(device_id, run if running else None, metrics)
            if running:
                self._add_dataset_metrics(run, metrics)
        device["metrics"] = metrics
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
            "error": localized(self.devices.last_error),
        }

    def _append_log(self, run, text):
        translations = None
        if isinstance(text, Text):
            translations = {language: text.render(language) for language in SUPPORTED_LOCALES}
        elif text.startswith(log.LOG_PREFIX):
            try:
                payload = json.loads(text[len(log.LOG_PREFIX):])
                if isinstance(payload, dict) and isinstance(payload.get("text"), str):
                    text = payload["text"]
                    candidate = payload.get("translations")
                    if isinstance(candidate, dict) and all(isinstance(candidate.get(language), str) for language in SUPPORTED_LOCALES):
                        translations = {language: candidate[language] for language in SUPPORTED_LOCALES}
            except (ValueError, TypeError):
                pass
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
        if translations:
            translations = {
                language: _ANSI.sub("", value).replace("\x00", "").strip("\r\n").encode("utf-8")[:MAX_LOG_LINE_BYTES].decode("utf-8", errors="ignore")
                for language, value in translations.items()
            }
        with self._lock:
            run.cursor += 1
            line = {"id": run.cursor, "time": _iso_time(time.time()), "text": text}
            if translations:
                line["translations"] = translations
            run.lines.append(line)
            run.log_bytes += self._log_size(line)
            run.log_truncated += int(truncated)
            self._trim_logs(run)

    @staticmethod
    def _log_size(line):
        return len(line["text"].encode("utf-8")) + sum(
            len(value.encode("utf-8")) for value in line.get("translations", {}).values()
        )

    @staticmethod
    def _trim_logs(run):
        while run.lines and (len(run.lines) > MAX_LOG_LINES or run.log_bytes > MAX_LOG_BYTES):
            removed = run.lines.popleft()
            run.log_bytes -= BotSupervisor._log_size(removed)
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
            self._append_log(run, t("messages.bot_output_read_failed", exc=exc))
        finally:
            if run.process.stdout is not None:
                run.process.stdout.close()

    def _watch_process(self, run):
        run.exit_code = run.process.wait()
        with self._lock:
            device_id = next(
                (key for key, active in self._runs.items() if active is run),
                None,
            )
            if device_id is not None:
                self._add_report_metrics(
                    device_id, run,
                    self._metrics(device_id, run, allow_finished=True),
                )
        self._cleanup_group(run)

        if device_id is None or not run.exit_code:
            return
        for attempt in range(run.restart_attempt + 1, config.BOT_RESTART_LIMIT + 1):
            with self._lock:
                if self._closing or run.stop_requested or self._runs.get(device_id) is not run:
                    return
            self._append_log(run, (
                t("messages.bot_recovery_scheduled", value_1=run.exit_code, attempt=attempt, value_3=config.BOT_RESTART_LIMIT, value_4=config.BOT_RESTART_DELAY)
            ))
            if run.recovery_cancel.wait(config.BOT_RESTART_DELAY):
                return
            try:
                with self._auto_training_lock, self._operation_lock(device_id):
                    if self._closing or run.stop_requested or self._runs.get(device_id) is not run:
                        return
                    if run.reader is not None:
                        run.reader.join(timeout=1)
                    self._start(device_id, restart_attempt=attempt)
                return
            except Exception as error:
                self._append_log(run, t("messages.recovery_attempt_failed", attempt=attempt, error=error))
        if not run.stop_requested and not self._closing:
            self._append_log(run, t("messages.bot_recovery_limit", value_1=config.BOT_RESTART_LIMIT))

    def _cleanup_group(self, run):
        FramePreview(run.status_file, run.session_id).clear()
        with self._lock:
            run.template_snapshots.clear()
        if run.pause_file is not None:
            try:
                run.pause_file.unlink(missing_ok=True)
            except OSError:
                pass
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

    def _start(self, device_id, restart_attempt=0):
        device = self._resolve(device_id)
        device_id = device["id"]
        with self._operation_lock(device_id):
            with self._lock:
                if self._closing:
                    raise RuntimeError(t("messages.dashboard_stopping"))
                previous = self._runs.get(device_id)
                if previous is not None and previous.process.poll() is None:
                    return self._describe(device)
                for running_id, active in self._runs.items():
                    if running_id != device_id and active.serial in device["aliases"] and active.process.poll() is None:
                        raise RuntimeError(t("messages.duplicate_adb_connection"))
            if not device["connected"]:
                raise RuntimeError(t("messages.device_not_authorized"))
            profile = migrate_legacy_blocks(self.store, device_id, device.get("aliases", []))
            profile_path = Path(self.store.path_for(device_id))
            if not profile_path.is_file():
                profile = self.store.save(device_id, profile)
            runtime = self.state_root / "runtime"
            runtime.mkdir(parents=True, exist_ok=True)
            key = hashlib.sha256(device_id.encode("utf-8")).hexdigest()
            status_file = runtime / f"{key}.status.json"
            stop_file = runtime / f"{key}.stop"
            pause_file = runtime / f"{key}.pause"
            for path in (status_file, stop_file, pause_file):
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
                "EATVENTURE_PAUSE_FILE": str(pause_file),
                "EATVENTURE_SESSION_ID": session_id,
                "EATVENTURE_AUTO_TRAIN_EXTERNAL": "1",
                "EATVENTURE_LOG_JSON": "1",
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
                    raise RuntimeError(t("messages.dashboard_stopping"))
                for running_id, active in self._runs.items():
                    if running_id != device_id and active.serial in device["aliases"] and active.process.poll() is None:
                        raise RuntimeError(t("messages.duplicate_adb_connection"))
                try:
                    dataset_base = self._sample_catalog()
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
                    raise RuntimeError(t("messages.bot_start_failed", exc=exc)) from exc
                run = _Run(
                    process, started_at, session_id, device["serial"], status_file,
                    stop_file, pause_file=pause_file,
                )
                run.restart_attempt = restart_attempt
                run.detector_mode = profile["run"]["detector_mode"]
                run.report_base = dict(self._report(device_id))
                run.dataset_base = dataset_base
                if previous:
                    run.lines.extend(previous.lines)
                    run.cursor = previous.cursor
                    run.log_bytes = previous.log_bytes
                    run.log_dropped = previous.log_dropped
                    run.log_truncated = previous.log_truncated
                    self._trim_logs(run)
                self._runs[device_id] = run
            self._append_log(run, t("messages.bot_started", value_1=process.pid))
            if restart_attempt:
                self._append_log(run, t("messages.bot_recovered", restart_attempt=restart_attempt, value_2=config.BOT_RESTART_LIMIT))
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
            raise RuntimeError(t("messages.bot_stop_failed")) from exc

    def stop(self, device_id):
        with self._auto_training_lock:
            return self._stop(device_id)

    def _stop(self, device_id):
        device = self._resolve(device_id)
        device_id = device["id"]
        with self._operation_lock(device_id):
            with self._lock:
                run = self._runs.get(device_id)
                if run is not None:
                    run.stop_requested = True
                    run.recovery_cancel.set()
                if run is None or run.process.poll() is not None:
                    if run is not None:
                        self._cleanup_group(run)
                    return self._describe(device)
                run.stopping = True
                run.stop_requested = True
            self._append_log(run, t("messages.dashboard_stop_requested"))
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
            self._append_log(run, t("messages.bot_stopped", value_1=run.exit_code))
            return self._describe(device)

    def restart(self, device_id):
        with self._auto_training_lock:
            device = self._resolve(device_id)
            with self._operation_lock(device["id"]):
                self.stop(device["id"])
                return self.start(device["id"])

    def request_vision(self, device_id, enabled, mode="vision"):
        if mode not in {"vision", "template", "clean"}:
            raise ValueError(t("messages.invalid_preview_mode"))
        device_id = self._runtime_device_id(device_id)
        with self._operation_lock(device_id):
            with self._lock:
                run = self._runs.get(device_id)
                if run is None or run.process.poll() is not None:
                    if enabled:
                        raise RuntimeError(t("messages.vision_requires_running_bot"))
                    if run is not None:
                        FramePreview(run.status_file, run.session_id).clear()
                        run.template_snapshots.clear()
                    return {"enabled": False}
            path = preview_paths(run.status_file)["request"]
            request = {"session_id": run.session_id, "expires_at": time.time() + 8 if enabled else 0, "mode": mode}
            temporary = None
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".vision-request-", delete=False) as output:
                    temporary = Path(output.name)
                    json.dump(request, output, allow_nan=False)
                os.replace(temporary, path)
            except OSError as error:
                raise RuntimeError(t("messages.vision_toggle_failed")) from error
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            if not enabled:
                FramePreview(run.status_file, run.session_id).clear()
                with self._lock:
                    run.template_snapshots.clear()
            if run.pause_file is not None:
                try:
                    if enabled and mode == "template":
                        run.pause_file.write_text(run.session_id, encoding="utf-8")
                    else:
                        run.pause_file.unlink(missing_ok=True)
                except OSError as error:
                    raise RuntimeError(t("messages.template_pause_failed")) from error
            return {"enabled": enabled}

    def vision_frame(self, device_id, mode="vision"):
        device_id = self._runtime_device_id(device_id)
        self.request_vision(device_id, True, mode)
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
                    or metadata.get("mode", "vision") != mode
                    or metadata.get("pid") != (run.worker_pid or run.process.pid)
                    or updated_at < run.started_at
                    or not -2 <= time.time() - updated_at <= 5
                    or not 0 < stat.st_size <= 5_000_000):
                raise ValueError(t("messages.preview_unavailable"))
            return paths["image"]
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise RuntimeError(t("messages.waiting_vision_frame")) from error

    def template_snapshot(self, device_id, snapshot_id=None):
        device_id = self._runtime_device_id(device_id)
        path = self.vision_frame(device_id, mode="template")
        try:
            content = path.read_bytes()
        except OSError as error:
            raise RuntimeError(t("messages.template_freeze_failed")) from error
        if not 0 < len(content) <= 5_000_000:
            raise RuntimeError(t("messages.invalid_template_frame"))
        if snapshot_id is not None and (
            not isinstance(snapshot_id, str)
            or re.fullmatch(r"[0-9a-f]{32}", snapshot_id) is None
        ):
            raise ValueError(t("messages.invalid_frame_id"))
        snapshot = {
            "id": snapshot_id or uuid.uuid4().hex,
            "content": content,
            "created_at": time.time(),
        }
        with self._lock:
            run = self._runs.get(device_id)
            if run is None or run.process.poll() is not None:
                raise RuntimeError(t("messages.bot_session_ended"))
            run.template_snapshots[snapshot["id"]] = snapshot
            while len(run.template_snapshots) > 4:
                run.template_snapshots.pop(next(iter(run.template_snapshots)))
        return snapshot

    def save_template(self, device_id, category, name, snapshot_id, x, y, width, height):
        import cv2
        import numpy as np
        import unicodedata

        if not isinstance(category, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", category) is None:
            raise ValueError(t("messages.invalid_template_category"))
        if not isinstance(name, str) or len(name) > 64 or (name and not name.isprintable()):
            raise ValueError(t("messages.invalid_template_name"))
        if category != "food" and name.strip():
            raise ValueError(t("messages.custom_name_requires_food"))
        normalized_name = unicodedata.normalize("NFKD", name.strip()).encode("ascii", "ignore").decode()
        normalized_name = re.sub(r"[^a-z0-9]+", "_", normalized_name.lower()).strip("_")
        if name.strip() and not normalized_name:
            raise ValueError(t("messages.template_name_required"))
        values = (x, y, width, height)
        if any(type(value) is not int for value in values) or width < 6 or height < 6:
            raise ValueError(t("messages.selection_too_small"))
        if not isinstance(snapshot_id, str) or re.fullmatch(r"[0-9a-f]{32}", snapshot_id) is None:
            raise ValueError(t("messages.invalid_selected_frame"))
        device_id = self._runtime_device_id(device_id)
        with self._lock:
            run = self._runs.get(device_id)
            snapshot = run.template_snapshots.get(snapshot_id) if run is not None else None
            if snapshot is None:
                raise RuntimeError(t("messages.selected_frame_expired"))
            content = snapshot["content"]
        image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError(t("messages.frozen_frame_unreadable"))
        frame_height, frame_width = image.shape[:2]
        if x < 0 or y < 0 or x + width > frame_width or y + height > frame_height:
            raise ValueError(t("messages.selection_outside_frame"))
        crop = image[y:y + height, x:x + width]
        folder = self.root / "src" / "vision" / "templates" / "default" / category
        with self._lock:
            folder.mkdir(parents=True, exist_ok=True)
            file_name = normalized_name or "item"
            numbered = re.compile(
                rf"^(?:\d+x\d+_)?{re.escape(file_name)}_(\d+)\.png$",
                re.IGNORECASE,
            )
            indexes = [
                int(match.group(1))
                for candidate in folder.glob("*.png")
                if (match := numbered.fullmatch(candidate.name)) is not None
            ]
            index = max(indexes, default=0) + 1
            destination = folder / f"{frame_width}x{frame_height}_{file_name}_{index:03d}.png"
            if not cv2.imwrite(str(destination), crop):
                raise RuntimeError(t("messages.template_save_failed"))
            if run.pause_file is not None:
                try:
                    run.pause_file.unlink(missing_ok=True)
                except OSError as error:
                    raise RuntimeError(t("messages.template_resume_failed")) from error
        return {"path": str(destination), "category": category, "width": width, "height": height}

    def open_vision_stream(self, device_id, mode="vision"):
        if mode not in {"vision", "clean"}:
            raise ValueError(t("messages.invalid_preview_mode"))
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
                raise RuntimeError(t("messages.vision_requires_running_bot"))
            raise KeyError(device_id)
        with self._operation_lock(identifier):
            with self._lock:
                run = self._runs.get(identifier)
                if run is None or run.process.poll() is not None:
                    raise RuntimeError(t("messages.vision_requires_running_bot"))
                session_id = run.session_id
            self.request_vision(identifier, True, mode)
            return {"device_id": identifier, "session_id": session_id}

    def next_vision_frame(self, device_id, session_id, after=None, renew=False, mode="vision"):
        def current_run():
            with self._lock:
                run = self._runs.get(device_id)
                if run is None or run.session_id != session_id or run.process.poll() is not None:
                    raise RuntimeError(t("messages.vision_session_ended"))
                return run

        run = current_run()
        if renew:
            with self._operation_lock(device_id):
                run = current_run()
                self.request_vision(device_id, True, mode)
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
            raise RuntimeError(t("messages.vision_session_ended"))
        return {"signature": signature, "content": content}

    def logs(self, device_id, after=0, limit=200):
        device_id = self._runtime_device_id(device_id)
        try:
            after = max(0, int(after))
            limit = min(1000, max(1, int(limit)))
        except (ValueError, TypeError) as exc:
            raise ValueError(t("messages.log_integer_required")) from exc
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
            for run in self._runs.values():
                run.recovery_cancel.set()
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

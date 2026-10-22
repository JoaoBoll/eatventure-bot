import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.device_profiles import ProfileStore
from dashboard.supervisor import BotSupervisor, DeviceManager, _Run, _network_address


def _device():
    return {
        "id": "hardware-1", "serial": "usb-1", "model": "Android",
        "connection": "usb", "adb_state": "device", "connected": True,
        "aliases": ["usb-1", "192.168.1.4:39015"],
    }


def _supervisor(tmp_path):
    devices = Mock()
    devices.list.return_value = [_device()]
    store = ProfileStore(tmp_path / "local" / "profiles")
    return BotSupervisor(tmp_path, store=store, devices=devices)


def test_construction_does_not_start_adb_or_processes(tmp_path):
    with patch("dashboard.supervisor.subprocess.run") as run, patch("dashboard.supervisor.subprocess.Popen") as popen:
        BotSupervisor(tmp_path)
    run.assert_not_called()
    popen.assert_not_called()
    assert not (tmp_path / "local").exists()


def test_discovery_groups_usb_and_wifi_and_preserves_unauthorized():
    output = (
        "List of devices attached\n"
        "192.168.1.4:39015 device model:Android\n"
        "usb-1 device model:Android\n"
        "usb-2 unauthorized usb:1-2\n"
    )

    def adb(_, args, timeout=8):
        if args == ["devices", "-l"]:
            return output
        return "hardware-1" if args[-1] == "ro.serialno" else "Android"

    with patch("dashboard.supervisor._adb_run", side_effect=adb):
        manager = DeviceManager(adb_path="adb")
        devices = manager.list()
    assert len(devices) == 2
    online = next(device for device in devices if device["connected"])
    assert online["id"] == "hardware-1"
    assert online["serial"] == "usb-1"
    assert set(online["aliases"]) == {"usb-1", "192.168.1.4:39015"}
    assert next(device for device in devices if device["id"] == "usb-2")["adb_state"] == "unauthorized"


@pytest.mark.parametrize("address", ["host:0", "host:65536", "-host:5555", "host;echo:5555", "host:5555\nattack", "host", "[invalid]:5555"])
def test_adb_addresses_are_validated_before_execution(address):
    with patch("dashboard.supervisor._adb_run") as run:
        with pytest.raises(ValueError):
            DeviceManager(adb_path="adb").connect(address)
    run.assert_not_called()


def test_ipv4_hostname_and_ipv6_addresses():
    for address in ("192.168.1.4:5555", "android.local:39015", "[::1]:5555"):
        assert _network_address(address) == address


def test_start_is_per_hardware_and_preserves_profile(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.store.save("hardware-1", {"name": "Meu Android", "run": {"ai_collect": True, "save_log": True}})
    profile_path = supervisor.store.path_for("hardware-1")
    original = profile_path.read_text(encoding="utf-8")
    process = Mock(pid=12345)
    process.poll.return_value = None
    with patch("dashboard.supervisor.subprocess.Popen", return_value=process) as popen, patch("dashboard.supervisor.threading.Thread"):
        first = supervisor.start("hardware-1")
        second = supervisor.start("192.168.1.4:39015")
    assert first["running"] and second["pid"] == 12345
    popen.assert_called_once()
    args, options = popen.call_args
    assert args[0][-3:] == ["usb-1", "--ai-collect", "--log"]
    assert options["env"]["EATVENTURE_DEVICE_ID"] == "hardware-1"
    assert options["env"]["EATVENTURE_PROFILE"] == str(profile_path)
    assert options["env"]["EATVENTURE_SESSION_ID"]
    assert not options.get("shell")
    assert profile_path.read_text(encoding="utf-8") == original


def test_linux_start_creates_a_separate_process_session(tmp_path):
    supervisor = _supervisor(tmp_path)
    process = Mock(pid=12345)
    process.poll.return_value = None
    linux = SimpleNamespace(name="posix", environ=os.environ, killpg=Mock())
    with patch("dashboard.supervisor.os", linux), patch("dashboard.supervisor.subprocess.Popen", return_value=process) as popen, patch("dashboard.supervisor.threading.Thread"):
        device = supervisor.start("hardware-1")
    assert device["running"]
    assert popen.call_args.kwargs["start_new_session"] is True
    assert "creationflags" not in popen.call_args.kwargs
    linux.killpg.assert_not_called()


def test_linux_cleanup_only_kills_selected_group_once(tmp_path):
    supervisor = _supervisor(tmp_path)
    process = Mock(pid=12345)
    process.poll.return_value = None
    linux = SimpleNamespace(name="posix", environ=os.environ, killpg=Mock())
    with patch("dashboard.supervisor.os", linux), patch("dashboard.supervisor.signal", SimpleNamespace(SIGKILL=9)), patch("dashboard.supervisor.subprocess.Popen", return_value=process), patch("dashboard.supervisor.threading.Thread"):
        supervisor.start("hardware-1")
        run = supervisor._runs["hardware-1"]
        supervisor._cleanup_group(run)
        supervisor._cleanup_group(run)
    linux.killpg.assert_called_once_with(12345, 9)
    process.kill.assert_not_called()
    process.send_signal.assert_not_called()


def test_unknown_device_cannot_create_process_or_profile(tmp_path):
    supervisor = _supervisor(tmp_path)
    with patch("dashboard.supervisor.subprocess.Popen") as popen:
        with pytest.raises(KeyError):
            supervisor.start("unknown")
    popen.assert_not_called()
    assert not supervisor.store.root.exists()


def test_metrics_reject_other_sessions_and_stale_data(tmp_path):
    supervisor = _supervisor(tmp_path)
    process = Mock(pid=12345)
    process.poll.return_value = None
    now = time.time()
    status_file = tmp_path / "status.json"
    run = _Run(process, now - 2, "session-1", "usb-1", status_file, tmp_path / "stop")
    status = {"pid": 12345, "device_id": "hardware-1", "session_id": "session-1", "updated_at": now, "templates": 156}
    status_file.write_text(json.dumps(status), encoding="utf-8")
    assert supervisor._metrics("hardware-1", run)["templates"] == 156
    status["session_id"] = "previous-session"
    status_file.write_text(json.dumps(status), encoding="utf-8")
    assert supervisor._metrics("hardware-1", run) == {}
    status.update(session_id="session-1", updated_at=now - 60)
    status_file.write_text(json.dumps(status), encoding="utf-8")
    assert supervisor._metrics("hardware-1", run) == {}


def test_logs_are_bounded_and_paginated(tmp_path):
    supervisor = _supervisor(tmp_path)
    process = Mock(pid=12345)
    process.poll.return_value = None
    run = _Run(process, time.time(), "session", "usb-1", tmp_path / "status", tmp_path / "stop")
    supervisor._runs["hardware-1"] = run
    for index in range(2100):
        supervisor._append_log(run, f"\x1b[32mlinha {index}\x1b[0m\n")
    page = supervisor.logs("hardware-1", after=100, limit=2)
    assert len(run.lines) == 2000
    assert [line["id"] for line in page["lines"]] == [101, 102]
    assert page["cursor"] == 102
    assert page["lines"][0]["text"] == "linha 100"


def test_saved_profile_remains_visible_without_adb(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.devices.list.return_value = []
    supervisor.store.save("hardware-1", {"name": "Cozinha"})
    device = supervisor.list_devices()[0]
    assert device["name"] == "Cozinha"
    assert device["status"] == "disconnected"
    assert device["metrics"] == {}


def test_stop_writes_session_request_and_only_waits_for_selected_process(tmp_path):
    supervisor = _supervisor(tmp_path)
    process = Mock(pid=12345)
    process.poll.return_value = None

    def finish(timeout=None):
        process.poll.return_value = 0
        return 0

    process.wait.side_effect = finish
    run = _Run(process, time.time(), "session-1", "usb-1", tmp_path / "status", tmp_path / "stop")
    other = Mock(pid=54321)
    other.poll.return_value = None
    supervisor._runs.update({
        "hardware-1": run,
        "hardware-2": _Run(other, time.time(), "session-2", "usb-2", tmp_path / "status-2", tmp_path / "stop-2"),
    })
    with patch.object(supervisor, "_cleanup_group"):
        result = supervisor.stop("hardware-1")
    assert run.stop_file.read_text(encoding="utf-8") == "session-1"
    assert result["status"] == "stopped"
    process.wait.assert_called_once_with(timeout=6)
    process.send_signal.assert_not_called()
    other.wait.assert_not_called()
    other.send_signal.assert_not_called()


def test_shutdown_blocks_new_starts(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.shutdown()
    with patch("dashboard.supervisor.subprocess.Popen") as popen:
        with pytest.raises(RuntimeError, match="encerrado"):
            supervisor.start("hardware-1")
    popen.assert_not_called()

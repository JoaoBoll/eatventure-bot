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
from core.preview import preview_paths
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
    devices.last_error = None
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


def test_mdns_reconnects_changed_wifi_port_and_uses_stable_device_name():
    current_port = [41000]
    device_queries = [0]
    connections = []
    service_name = "adb-e2615705-2D2Z90"

    def adb(_, args, timeout=8):
        if args == ["mdns", "services"]:
            return (
                "List of discovered mdns services\n"
                f"{service_name}._adb-tls-connect._tcp _adb-tls-connect._tcp 192.168.1.4:{current_port[0]}\n"
            )
        if args == ["devices", "-l"]:
            query = device_queries[0]
            device_queries[0] += 1
            if query % 2 == 0:
                return "List of devices attached\n192.168.1.4:39015 offline model:Android\n"
            return f"List of devices attached\n192.168.1.4:{current_port[0]} device model:Android\n"
        if args[0] == "connect":
            connections.append(args[1])
            return f"connected to {args[1]}"
        if args[-1] in {"ro.serialno", "ro.boot.serialno"}:
            return "unknown"
        return "Android Phone"

    with patch("dashboard.supervisor._adb_run", side_effect=adb):
        manager = DeviceManager(adb_path="adb", cache_seconds=0)
        first = manager.list()
        current_port[0] = 42000
        second = manager.list(refresh=True)

    assert connections == ["192.168.1.4:41000", "192.168.1.4:42000"]
    assert first[0]["id"] == second[0]["id"] == service_name
    assert first[0]["serial"] == "192.168.1.4:41000"
    assert second[0]["serial"] == "192.168.1.4:42000"
    assert "192.168.1.4:42000" in second[0]["aliases"]


def test_discovery_merges_old_wifi_port_into_current_connection():
    def adb(_, args, timeout=8):
        if args == ["mdns", "services"]:
            return "List of discovered mdns services\n"
        if args == ["devices", "-l"]:
            return (
                "List of devices attached\n"
                "192.168.1.4:42819 offline model:Android\n"
                "192.168.1.4:5555 device model:Android\n"
            )
        if args[-1] in {"ro.serialno", "ro.boot.serialno"}:
            return "unknown"
        return "Android"

    with patch("dashboard.supervisor._adb_run", side_effect=adb):
        devices = DeviceManager(adb_path="adb").list()

    assert len(devices) == 1
    assert devices[0]["serial"] == "192.168.1.4:5555"
    assert devices[0]["connected"]
    assert set(devices[0]["aliases"]) == {"192.168.1.4:42819", "192.168.1.4:5555"}


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
    assert len(run.lines) == 500
    assert [line["id"] for line in page["lines"]] == [1601, 1602]
    assert page["cursor"] == 1602
    assert page["lines"][0]["text"] == "linha 1600"


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


def test_auto_training_stops_running_bots_and_restarts_them(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.store.save("hardware-1", {"run": {"ai_collect": False}})
    process = Mock(pid=12345)
    process.poll.return_value = None
    supervisor._runs["hardware-1"] = _Run(
        process, time.time(), "session-1", "usb-1",
        tmp_path / "status", tmp_path / "stop",
    )
    with (
        patch.object(supervisor, "stop") as stop,
        patch.object(supervisor, "start") as start,
    ):
        with supervisor._training_session("1080x2400"):
            stop.assert_called_once_with("hardware-1")
            start.assert_not_called()
        start.assert_called_once_with("hardware-1")


def test_auto_training_starts_for_existing_samples_without_collection(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.store.save(
        "hardware-1",
        {"run": {"ai_collect": False}, "settings": {"ai_auto_train": True}},
    )
    process = Mock(pid=12345)
    process.poll.return_value = None
    supervisor._runs["hardware-1"] = _Run(
        process, time.time(), "session-1", "usb-1",
        tmp_path / "status", tmp_path / "stop",
    )
    with patch("dataset.auto_train.AutoTrainer") as trainer_class:
        supervisor._ensure_auto_trainer()
    trainer_class.return_value.start.assert_called_once_with()


def test_shutdown_blocks_new_starts(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.shutdown()
    with patch("dashboard.supervisor.subprocess.Popen") as popen:
        with pytest.raises(RuntimeError, match="encerrado"):
            supervisor.start("hardware-1")
    popen.assert_not_called()


def _preview_run(tmp_path):
    supervisor = _supervisor(tmp_path)
    process = Mock(pid=12345)
    process.poll.return_value = None
    run = _Run(process, 990.0, "session-preview", "usb-1", tmp_path / "current.status.json", tmp_path / "stop")
    supervisor._runs["hardware-1"] = run
    return supervisor, run, preview_paths(run.status_file)


def _preview_files(paths, run, **metadata):
    paths["image"].write_bytes(b"simulated-jpeg-frame")
    paths["metadata"].write_text(json.dumps({
        "session_id": run.session_id, "pid": run.process.pid, "updated_at": 999.0, **metadata,
    }), encoding="utf-8")


def test_request_vision_atomically_creates_lease_for_current_session(tmp_path):
    supervisor, run, paths = _preview_run(tmp_path)
    old_request = {"session_id": "previous-session", "expires_at": 0}
    paths["request"].write_text(json.dumps(old_request), encoding="utf-8")
    original_replace = os.replace
    replacements = []

    def check_replace(source, destination):
        assert Path(destination) == paths["request"]
        assert Path(source).parent == paths["request"].parent
        assert json.loads(paths["request"].read_text(encoding="utf-8")) == old_request
        assert json.loads(Path(source).read_text(encoding="utf-8")) == {
            "session_id": run.session_id, "expires_at": 1008.0,
        }
        replacements.append(Path(source))
        original_replace(source, destination)

    with patch("dashboard.supervisor.time.time", return_value=1000.0), patch(
        "dashboard.supervisor.os.replace", side_effect=check_replace,
    ):
        result = supervisor.request_vision("hardware-1", True)

    assert result["enabled"] is True
    assert len(replacements) == 1
    assert not replacements[0].exists()
    assert json.loads(paths["request"].read_text(encoding="utf-8")) == {
        "session_id": run.session_id, "expires_at": 1008.0,
    }
    run.process.send_signal.assert_not_called()


def test_disabling_vision_expires_lease_and_clears_only_current_session(tmp_path):
    supervisor, run, paths = _preview_run(tmp_path)
    _preview_files(paths, run)

    result = supervisor.request_vision("hardware-1", False)

    assert result["enabled"] is False
    assert json.loads(paths["request"].read_text(encoding="utf-8")) == {
        "session_id": run.session_id, "expires_at": 0,
    }
    assert not paths["image"].exists()
    assert not paths["metadata"].exists()


def test_disabling_vision_does_not_remove_files_owned_by_another_session(tmp_path):
    supervisor, run, paths = _preview_run(tmp_path)
    _preview_files(paths, run, session_id="other-session")

    assert supervisor.request_vision("hardware-1", False)["enabled"] is False
    assert paths["image"].exists()
    assert json.loads(paths["metadata"].read_text(encoding="utf-8"))["session_id"] == "other-session"


def test_disabling_vision_without_a_run_does_not_start_or_write_anything(tmp_path):
    supervisor = _supervisor(tmp_path)
    with patch("dashboard.supervisor.subprocess.Popen") as popen:
        assert supervisor.request_vision("hardware-1", False)["enabled"] is False
    popen.assert_not_called()
    assert not supervisor.state_root.exists()


def test_disabling_vision_for_stopped_run_is_allowed(tmp_path):
    supervisor, run, paths = _preview_run(tmp_path)
    run.process.poll.return_value = 0
    assert supervisor.request_vision("hardware-1", False)["enabled"] is False
    run.process.send_signal.assert_not_called()


@pytest.mark.parametrize("with_run", [False, True])
def test_enabling_vision_requires_a_live_run(tmp_path, with_run):
    supervisor, run, paths = _preview_run(tmp_path)
    if with_run:
        run.process.poll.return_value = 0
    else:
        supervisor._runs.clear()
    with pytest.raises(RuntimeError):
        supervisor.request_vision("hardware-1", True)
    assert not paths["request"].exists()


def test_vision_frame_returns_current_image_and_renews_lease(tmp_path):
    supervisor, run, paths = _preview_run(tmp_path)
    _preview_files(paths, run)

    with patch("dashboard.supervisor.time.time", return_value=1000.0):
        frame = supervisor.vision_frame("hardware-1")

    assert frame == paths["image"]
    assert json.loads(paths["request"].read_text(encoding="utf-8")) == {
        "session_id": run.session_id, "expires_at": 1008.0,
    }


@pytest.mark.parametrize("metadata", [
    {"session_id": "previous-session"},
    {"pid": 54321},
    {"updated_at": 994.0},
    {"updated_at": float("nan")},
])
def test_vision_frame_rejects_other_process_sessions_and_stale_metadata(tmp_path, metadata):
    supervisor, run, paths = _preview_run(tmp_path)
    _preview_files(paths, run, **metadata)
    with patch("dashboard.supervisor.time.time", return_value=1000.0), pytest.raises(RuntimeError):
        supervisor.vision_frame("hardware-1")


def test_vision_frame_rejects_metadata_older_than_process_start(tmp_path):
    supervisor, run, paths = _preview_run(tmp_path)
    run.started_at = 999.0
    _preview_files(paths, run, updated_at=998.5)
    with patch("dashboard.supervisor.time.time", return_value=1000.0), pytest.raises(RuntimeError):
        supervisor.vision_frame("hardware-1")


def test_vision_frame_rejects_oversized_image(tmp_path):
    supervisor, run, paths = _preview_run(tmp_path)
    _preview_files(paths, run)
    with paths["image"].open("ab") as image:
        image.truncate(6 * 1024 * 1024)
    with patch("dashboard.supervisor.time.time", return_value=1000.0), pytest.raises(RuntimeError):
        supervisor.vision_frame("hardware-1")


@pytest.mark.parametrize("missing", ["image", "metadata", "both"])
def test_vision_frame_reports_pending_until_image_and_metadata_exist(tmp_path, missing):
    supervisor, run, paths = _preview_run(tmp_path)
    _preview_files(paths, run)
    keys = ("image", "metadata") if missing == "both" else (missing,)
    for key in keys:
        paths[key].unlink()
    with patch("dashboard.supervisor.time.time", return_value=1000.0), pytest.raises(RuntimeError):
        supervisor.vision_frame("hardware-1")


@pytest.mark.parametrize("with_run", [False, True])
def test_vision_frame_requires_live_current_process(tmp_path, with_run):
    supervisor, run, paths = _preview_run(tmp_path)
    _preview_files(paths, run)
    if with_run:
        run.process.poll.return_value = 0
    else:
        supervisor._runs.clear()
    with patch("dashboard.supervisor.time.time", return_value=1000.0), pytest.raises(RuntimeError):
        supervisor.vision_frame("hardware-1")


def _runtime_status(run, **values):
    return {
        "device_id": "hardware-1", "session_id": run.session_id,
        "pid": run.process.pid, "updated_at": 1000.0,
        "fps": 12.5, "capture_fps": 30.0, "voos": 2, "reformas": 3, "acoes": 40,
        **values,
    }


def test_metrics_accept_and_pin_worker_pid_distinct_from_windows_launcher(tmp_path):
    supervisor, run, paths = _preview_run(tmp_path)
    status = _runtime_status(run, pid=23456)
    run.status_file.write_text(json.dumps(status), encoding="utf-8")
    with patch("dashboard.supervisor.time.time", return_value=1000.0):
        assert supervisor._metrics("hardware-1", run)["fps"] == 12.5
        assert run.worker_pid == 23456
        status["pid"] = 34567
        run.status_file.write_text(json.dumps(status), encoding="utf-8")
        assert supervisor._metrics("hardware-1", run) == {}
    assert run.worker_pid == 23456


def test_other_session_cannot_pin_the_worker_pid(tmp_path):
    supervisor, run, paths = _preview_run(tmp_path)
    run.status_file.write_text(json.dumps(_runtime_status(
        run, pid=23456, session_id="previous-session",
    )), encoding="utf-8")
    with patch("dashboard.supervisor.time.time", return_value=1000.0):
        assert supervisor._metrics("hardware-1", run) == {}
    assert run.worker_pid is None


@pytest.mark.parametrize("worker_pid", [0, -1, True, "23456", 23456.5])
def test_invalid_worker_pid_cannot_be_pinned(tmp_path, worker_pid):
    supervisor, run, paths = _preview_run(tmp_path)
    run.status_file.write_text(json.dumps(_runtime_status(run, pid=worker_pid)), encoding="utf-8")
    with patch("dashboard.supervisor.time.time", return_value=1000.0):
        assert supervisor._metrics("hardware-1", run) == {}
    assert run.worker_pid is None


def test_vision_frame_accepts_authenticated_worker_pid_from_telemetry(tmp_path):
    supervisor, run, paths = _preview_run(tmp_path)
    run.status_file.write_text(json.dumps(_runtime_status(run, pid=23456)), encoding="utf-8")
    _preview_files(paths, run, pid=23456)
    with patch("dashboard.supervisor.time.time", return_value=1000.0):
        assert supervisor.vision_frame("hardware-1") == paths["image"]
    assert run.worker_pid == 23456


@pytest.mark.parametrize("identifier", ["hardware-1", "usb-1"])
def test_vision_for_owned_running_device_does_not_query_adb(tmp_path, identifier):
    supervisor, run, paths = _preview_run(tmp_path)
    _preview_files(paths, run)
    supervisor.devices.list.side_effect = AssertionError("A prévia não deve aguardar a descoberta ADB.")
    with patch("dashboard.supervisor.time.time", return_value=1000.0):
        assert supervisor.request_vision(identifier, True)["enabled"] is True
        assert supervisor.vision_frame(identifier) == paths["image"]
    supervisor.devices.list.assert_not_called()


def test_device_description_reads_live_metrics_without_discovery_or_cache(tmp_path):
    supervisor, run, paths = _preview_run(tmp_path)
    supervisor.devices.list.side_effect = AssertionError("Atualizar métricas não deve consultar o ADB.")
    status = _runtime_status(run)
    with patch("dashboard.supervisor.time.time", return_value=1000.0):
        run.status_file.write_text(json.dumps(status), encoding="utf-8")
        first = supervisor._describe(_device())["metrics"]
        assert (first["fps"], first["capture_fps"], first["voos"], first["reformas"], first["acoes"]) == (12.5, 30.0, 2, 3, 40)
        status.update(fps=18.0, voos=4, reformas=5, acoes=60)
        run.status_file.write_text(json.dumps(status), encoding="utf-8")
        second = supervisor._describe(_device())["metrics"]
    assert (second["fps"], second["voos"], second["reformas"], second["acoes"]) == (18.0, 4, 5, 60)
    supervisor.devices.list.assert_not_called()


def test_runtime_state_refreshes_owned_runs_without_discovery(tmp_path):
    supervisor, run, paths = _preview_run(tmp_path)
    supervisor.devices.list.side_effect = AssertionError("O polling não deve consultar o ADB.")
    status = _runtime_status(run)
    run.status_file.write_text(json.dumps(status), encoding="utf-8")
    with patch("dashboard.supervisor.time.time", return_value=1000.0):
        first = supervisor.runtime_state()
        assert first["error"] is None
        assert first["devices"][0]["id"] == "hardware-1"
        assert first["devices"][0]["running"] is True
        assert first["devices"][0]["metrics"]["voos"] == 2
        status.update(fps=18.0, voos=4)
        run.status_file.write_text(json.dumps(status), encoding="utf-8")
        second = supervisor.runtime_state()
    assert second["devices"][0]["metrics"]["fps"] == 18.0
    assert second["devices"][0]["metrics"]["voos"] == 4
    supervisor.devices.list.assert_not_called()


def test_runtime_state_preserves_cached_devices_without_discovery(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.list_devices()
    supervisor.devices.list.reset_mock()
    supervisor.devices.list.side_effect = AssertionError("O polling deve usar o catálogo existente.")

    result = supervisor.runtime_state()

    assert [device["id"] for device in result["devices"]] == ["hardware-1"]
    assert result["devices"][0]["running"] is False
    supervisor.devices.list.assert_not_called()


def test_require_owned_device_does_not_query_adb(tmp_path):
    supervisor, run, paths = _preview_run(tmp_path)
    supervisor.devices.list.side_effect = AssertionError("O processo já pertence ao supervisor.")

    supervisor.require_device("hardware-1")

    supervisor.devices.list.assert_not_called()


def test_require_cached_device_does_not_query_adb(tmp_path):
    supervisor = _supervisor(tmp_path)
    supervisor.list_devices()
    supervisor.devices.list.reset_mock()
    supervisor.devices.list.side_effect = AssertionError("O dispositivo já está no catálogo.")

    supervisor.require_device("hardware-1")

    supervisor.devices.list.assert_not_called()


@pytest.mark.parametrize("identifier", ["hardware-1", "usb-1"])
def test_owned_run_logs_do_not_query_adb(tmp_path, identifier):
    supervisor, run, paths = _preview_run(tmp_path)
    supervisor._append_log(run, "linha de execução")
    supervisor.devices.list.side_effect = AssertionError("O polling de logs não deve consultar o ADB.")

    result = supervisor.logs(identifier)

    assert result["lines"][0]["text"] == "linha de execução"
    assert result["cursor"] == 1
    supervisor.devices.list.assert_not_called()

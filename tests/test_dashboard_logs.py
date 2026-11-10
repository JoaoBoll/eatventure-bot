"""Retenção dos logs em memória, com processos e descoberta simulados."""

from pathlib import Path
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.device_profiles import ProfileStore
from dashboard.supervisor import (
    BotSupervisor, MAX_LOG_BYTES, MAX_LOG_LINE_BYTES, MAX_LOG_LINES, _Run,
)


def _supervisor_run(tmp_path):
    devices = Mock(last_error=None)
    devices.list.return_value = [{
        "id": "hardware-1", "serial": "usb-1", "model": "Android", "connection": "usb",
        "adb_state": "device", "connected": True, "aliases": ["usb-1"],
    }]
    supervisor = BotSupervisor(tmp_path, store=ProfileStore(tmp_path / "profiles"), devices=devices)
    process = Mock(pid=12345)
    process.poll.return_value = None
    run = _Run(process, 1000.0, "session-1", "usb-1", tmp_path / "status.json", tmp_path / "stop")
    supervisor._runs["hardware-1"] = run
    return supervisor, run


def _retained_bytes(run):
    return sum(len(line["text"].encode("utf-8")) for line in run.lines)


def test_log_line_limit_keeps_latest_entries_and_monotonic_ids(tmp_path):
    supervisor, run = _supervisor_run(tmp_path)
    for index in range(MAX_LOG_LINES + 20):
        supervisor._append_log(run, f"linha {index}")

    assert len(run.lines) == MAX_LOG_LINES == 500
    assert run.lines[0]["id"] == 21
    assert run.lines[-1]["id"] == MAX_LOG_LINES + 20
    assert run.cursor == MAX_LOG_LINES + 20
    assert run.log_dropped == 20
    assert run.log_bytes == _retained_bytes(run)


def test_log_truncation_preserves_utf8_and_counts_encoded_bytes(tmp_path):
    supervisor, run = _supervisor_run(tmp_path)
    supervisor._append_log(run, "\x1b[32m" + "á😀" * 2000 + "\x1b[0m\n")

    text = run.lines[0]["text"]
    encoded = text.encode("utf-8")
    assert len(encoded) <= MAX_LOG_LINE_BYTES == 4096
    assert encoded.decode("utf-8") == text
    assert text.endswith("…")
    assert "\x1b" not in text
    assert run.log_bytes == len(encoded)
    assert run.log_truncated == 1


def test_total_log_byte_limit_applies_before_line_limit(tmp_path):
    supervisor, run = _supervisor_run(tmp_path)
    for _ in range(MAX_LOG_LINES):
        supervisor._append_log(run, "x" * MAX_LOG_LINE_BYTES)

    assert run.log_bytes == _retained_bytes(run) <= MAX_LOG_BYTES == 1024 * 1024
    assert len(run.lines) == MAX_LOG_BYTES // MAX_LOG_LINE_BYTES < MAX_LOG_LINES
    assert run.log_dropped == MAX_LOG_LINES - len(run.lines)
    assert run.cursor == MAX_LOG_LINES
    assert run.log_truncated == 0


def test_log_cursor_skips_discarded_entries_and_still_paginates(tmp_path):
    supervisor, run = _supervisor_run(tmp_path)
    for index in range(MAX_LOG_LINES + 20):
        supervisor._append_log(run, f"linha {index}")

    first = supervisor.logs("hardware-1", after=1, limit=2)
    second = supervisor.logs("hardware-1", after=first["cursor"], limit=2)
    empty = supervisor.logs("hardware-1", after=run.cursor)

    assert [line["id"] for line in first["lines"]] == [21, 22]
    assert [line["id"] for line in second["lines"]] == [23, 24]
    assert empty["lines"] == []
    assert empty["cursor"] == run.cursor
    assert first["retention"]["buffer_lines"] == MAX_LOG_LINES
    assert first["retention"]["buffer_bytes"] == _retained_bytes(run)
    assert first["retention"]["dropped"] == 20


def test_log_retention_is_independent_for_each_device(tmp_path):
    supervisor, first = _supervisor_run(tmp_path)
    second = _Run(Mock(pid=23456), 1000.0, "session-2", "usb-2", tmp_path / "status-2", tmp_path / "stop-2")
    supervisor._runs["hardware-2"] = second
    supervisor._append_log(second, "log do segundo dispositivo")
    for _ in range(MAX_LOG_LINES + 20):
        supervisor._append_log(first, "x" * MAX_LOG_LINE_BYTES)

    assert len(second.lines) == 1
    assert second.lines[0]["text"] == "log do segundo dispositivo"
    assert second.log_bytes == _retained_bytes(second)
    assert second.log_dropped == 0
    assert first.log_bytes <= MAX_LOG_BYTES


def test_restart_preserves_cursor_and_recounts_retained_logs(tmp_path):
    supervisor, previous = _supervisor_run(tmp_path)
    for _ in range(MAX_LOG_LINES + 20):
        supervisor._append_log(previous, "á" * MAX_LOG_LINE_BYTES)
    old_cursor = previous.cursor
    old_dropped = previous.log_dropped
    old_truncated = previous.log_truncated
    previous.process.poll.return_value = 0
    process = Mock(pid=23456)
    process.poll.return_value = None

    with patch("dashboard.supervisor.subprocess.Popen", return_value=process), patch(
        "dashboard.supervisor.threading.Thread",
    ):
        supervisor.start("hardware-1")

    current = supervisor._runs["hardware-1"]
    assert current is not previous
    assert current.cursor == old_cursor + 1
    assert current.log_bytes == _retained_bytes(current) <= MAX_LOG_BYTES
    assert len(current.lines) <= MAX_LOG_LINES
    assert current.log_dropped >= old_dropped
    assert current.log_truncated == old_truncated
    supervisor._append_log(current, "nova execução")
    assert current.cursor == old_cursor + 2
    assert current.log_bytes == _retained_bytes(current) <= MAX_LOG_BYTES
    assert [line["id"] for line in supervisor.logs("hardware-1", after=old_cursor)["lines"]] == [old_cursor + 1, old_cursor + 2]

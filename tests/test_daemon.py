"""Daemon: real Unix-socket server in a thread, real worker subprocess (fake models), real FSEvents watcher."""

import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
from conftest import write

from lantern.api import protocol
from lantern.embedding.fake import FakeEmbedder
from lantern.indexer import run_queue
from lantern.queue import jobs
from lantern.scanner.diff import scan_root
from lantern.service import power, settings
from lantern.service.core import Daemon
from lantern.service.power import PowerState
from lantern.store import db


def wait_until(cond, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def sock(monkeypatch):
    path = Path(tempfile.mkdtemp(prefix="ln", dir="/tmp")) / "d.sock"  # Unix socket paths are limited to 104 bytes
    monkeypatch.setenv("LANTERN_SOCKET", str(path))
    return path


@pytest.fixture
def start_daemon(sock, lantern_home):
    started = []

    def start(mode="ask", on_battery=False, debounce=0.2):
        s = settings.Settings(index_mode=mode, notify=False, debounce_s=debounce)
        settings.save(s)
        d = Daemon(
            embedder=FakeEmbedder(),
            power_reader=lambda: PowerState(on_battery=on_battery),
            worker_env={"LANTERN_EMBEDDER": "fake"},
        )
        t = threading.Thread(target=d.run, daemon=True)
        t.start()
        assert wait_until(lambda: protocol.running()), "daemon did not start"
        started.append((d, t))
        return d

    yield start
    for d, t in started:
        d.stopping.set()
        t.join(timeout=30)


def files_by_status():
    return protocol.call("status")["files"]


def register_root(root: Path) -> None:
    conn = db.connect(db.Path(os.environ["LANTERN_HOME"]) / "index.db")
    db.ensure_root(conn, root.resolve())
    conn.close()


def test_socket_is_user_only(start_daemon, sock):
    start_daemon()
    assert stat.S_IMODE(sock.stat().st_mode) == 0o600
    assert protocol.call("ping")["ok"]


def test_ask_mode_waits_for_approval_after_downtime(start_daemon, project):
    register_root(project)  # folder known from an earlier run; files added while the daemon was off
    start_daemon(mode="ask")
    s = protocol.call("status")
    assert s["awaiting_approval"] and sum(s["queued"].values()) == 3
    time.sleep(1.0)
    assert not protocol.call("status")["worker"]["running"]
    assert files_by_status().get("indexed", 0) == 0

    protocol.call("approve")
    assert wait_until(lambda: files_by_status().get("indexed") == 3)
    assert wait_until(lambda: not protocol.call("status")["approved"])  # back to asking once the run ends


def test_decline_keeps_files_waiting(start_daemon, project):
    register_root(project)
    start_daemon(mode="ask")
    protocol.call("decline")
    p = protocol.call("pending")
    assert not p["awaiting_approval"] and sum(p["queued"].values()) == 3


def test_auto_mode_and_watcher_pick_up_new_files(start_daemon, project):
    start_daemon(mode="auto")
    protocol.call("index", {"path": str(project)})
    assert wait_until(lambda: files_by_status().get("indexed") == 3)
    write(project, "notes/nowy.md", "# Nowa notatka o kotach w butach\n")
    assert wait_until(lambda: files_by_status().get("indexed") == 4, timeout=30), "watcher did not trigger a scan"


def test_watcher_in_ask_mode_asks_again(start_daemon, project):
    start_daemon(mode="ask")
    protocol.call("index", {"path": str(project)})  # explicit request counts as approval
    assert wait_until(lambda: files_by_status().get("indexed") == 3)
    # Changes during an approved run are included in it; after the run ends, new changes need approval again.
    assert wait_until(lambda: not protocol.call("status")["approved"])
    write(project, "nowy.md", "# Nowy plik\n")
    assert wait_until(lambda: protocol.call("status")["awaiting_approval"], timeout=30)


def test_search_through_the_api(start_daemon, project):
    start_daemon(mode="auto")
    protocol.call("index", {"path": str(project)})
    assert wait_until(lambda: files_by_status().get("indexed") == 3)
    r = protocol.call("search", {"query": "backoff_wrapper", "limit": 3})
    assert r["results"][0]["path"].endswith("net.py") and r["event_id"]


def test_manual_pause_blocks_the_worker(start_daemon, project):
    register_root(project)
    start_daemon(mode="ask")
    protocol.call("pause")
    protocol.call("approve")
    time.sleep(1.5)
    assert files_by_status().get("indexed", 0) == 0
    protocol.call("resume")
    assert wait_until(lambda: files_by_status().get("indexed") == 3)


def test_battery_pauses_indexing(start_daemon, project):
    register_root(project)
    start_daemon(mode="auto", on_battery=True)
    assert protocol.call("status")["paused"] == "battery"
    time.sleep(1.0)
    assert files_by_status().get("indexed", 0) == 0


def test_remove_and_shutdown(start_daemon, project, sock):
    d = start_daemon(mode="auto")
    protocol.call("index", {"path": str(project)})
    assert wait_until(lambda: files_by_status().get("indexed") == 3)
    assert protocol.call("remove", {"path": str(project)})["files"] == 3
    assert protocol.call("status")["files"] == {}
    protocol.call("shutdown")
    assert wait_until(lambda: not sock.exists())
    assert d.stopping.is_set()


def test_unknown_method_returns_error(start_daemon):
    start_daemon()
    with pytest.raises(protocol.DaemonError, match="unknown method"):
        protocol.call("format_disk")


def test_cli_talks_to_the_daemon(start_daemon, project, sock, lantern_home):
    start_daemon(mode="auto")
    env = {**os.environ, "LANTERN_SOCKET": str(sock), "LANTERN_HOME": str(lantern_home)}
    out = subprocess.run(
        [sys.executable, "-m", "lantern.cli", "index", str(project)], capture_output=True, text=True, env=env
    )
    assert out.returncode == 0 and "in the background" in out.stdout
    assert wait_until(lambda: files_by_status().get("indexed") == 3)
    out = subprocess.run([sys.executable, "-m", "lantern.cli", "status"], capture_output=True, text=True, env=env)
    assert "daemon: running" in out.stdout and "mode auto" in out.stdout


# ---------- units ----------


def test_should_stop_returns_unprocessed_jobs_to_the_queue(conn, project, embedder):
    scan_root(conn, project)
    stats = run_queue(conn, embedder, should_stop=lambda: True)
    assert stats.stopped and stats.indexed == 0
    assert jobs.counts(conn) == {"queued": 3}


def test_pmset_parsers():
    assert power.parse_batt("Now drawing from 'Battery Power'\n -InternalBattery-0 87%; discharging")
    assert not power.parse_batt("Now drawing from 'AC Power'\n -InternalBattery-0 100%; charged")
    assert not power.parse_therm(
        "Note: No thermal warning level has been recorded\nNote: No performance warning level has been recorded"
    )
    assert power.parse_therm(
        "CPU Power notify\n\tCPU_Scheduler_Limit \t= 100\n\tCPU_Available_CPUs \t= 10\n\tCPU_Speed_Limit \t= 70"
    )
    assert power.parse_therm("Thermal warning level set to 2.")


def test_settings_roundtrip_and_validation(lantern_home):
    s = settings.load()
    assert s.index_mode == "ask"
    settings.update(s, "index_mode", "auto")
    settings.update(s, "pause_on_battery", "false")
    settings.save(s)
    loaded = settings.load()
    assert loaded.index_mode == "auto" and loaded.pause_on_battery is False
    with pytest.raises(ValueError):
        settings.update(loaded, "index_mode", "sometimes")
    with pytest.raises(KeyError):
        settings.update(loaded, "telemetry", "true")


def test_data_dir_is_user_only_and_shared_socket_dir_untouched(start_daemon, sock, lantern_home):
    os.chmod(sock.parent, 0o755)  # e.g. a shared directory chosen via LANTERN_SOCKET
    start_daemon()
    assert stat.S_IMODE(lantern_home.stat().st_mode) == 0o700
    assert stat.S_IMODE(sock.parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(sock.stat().st_mode) == 0o600


def test_files_left_waiting_by_an_earlier_run_are_asked_about_after_restart(start_daemon, project):
    register_root(project)
    d = start_daemon(mode="ask")
    protocol.call("decline")
    d.stopping.set()
    assert wait_until(lambda: not protocol.running())
    start_daemon(mode="ask")  # nothing new on disk, but 3 files still wait
    assert protocol.call("status")["awaiting_approval"]

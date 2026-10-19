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

from lantern import config
from lantern.api import protocol
from lantern.embedding.fake import FakeEmbedder
from lantern.indexer import run_queue
from lantern.queue import jobs
from lantern.scanner.diff import scan_root
from lantern.service import power, qos, settings
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

    def start(mode="ask", on_battery=False, debounce=0.2, power_state=None):
        s = settings.Settings(index_mode=mode, notify=False, debounce_s=debounce)
        settings.save(s)
        d = Daemon(
            embedder=FakeEmbedder(),
            power_reader=lambda: power_state or PowerState(on_battery=on_battery),
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
    assert r["corrected_query"] is None
    r = protocol.call("search", {"query": "przycinanie drzwa", "spelling": {"drzwa": ["drzwi", "drzewa"]}})
    assert r["corrected_query"] == "przycinanie drzewa" and r["results"][0]["path"].endswith("README.md")
    passage = protocol.call("passage", {"chunk_id": r["results"][0]["chunk_id"]})  # the whole passage, for previews
    assert passage["path"].endswith("README.md") and "drzew" in passage["text"].lower()


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


def test_low_power_mode_pauses_indexing_unless_switched_off(start_daemon, project):
    register_root(project)
    start_daemon(mode="auto", power_state=PowerState(low_power=True, thermal="fair"))
    status = protocol.call("status")
    assert status["paused"] == "low_power" and status["power"]["low_power"] and status["power"]["thermal"] == "fair"
    protocol.call("config_set", {"key": "pause_on_low_power", "value": "false"})
    assert wait_until(lambda: files_by_status().get("indexed") == 3)


def test_status_with_the_fake_photo_model(start_daemon):
    from lantern.embedding.fake import FakeImageEmbedder

    d = start_daemon()
    d.query_embedder = FakeImageEmbedder()  # what LANTERN_EMBEDDER=fake gives; it has no `loaded` flag
    assert protocol.call("status")["photo_model_loaded"] is False


def test_folder_added_while_settings_are_open_waits_until_they_close(start_daemon, project, monkeypatch):
    start_daemon(mode="auto")
    protocol.call("status", {"settings_open": True})
    protocol.call("index", {"path": str(project)})
    time.sleep(1.5)
    status = protocol.call("status")  # the CLI does not report the window: the hold stays
    assert status["waiting_for_settings"] and files_by_status().get("indexed", 0) == 0
    protocol.call("status", {"settings_open": False})  # window closed = go ahead
    assert wait_until(lambda: files_by_status().get("indexed") == 3)
    assert not protocol.call("status")["waiting_for_settings"]


def test_settings_hold_expires_when_the_app_goes_silent(start_daemon, project, monkeypatch):
    from lantern.service import core

    monkeypatch.setattr(core, "SETTINGS_HOLD_S", 1.0)
    start_daemon(mode="auto")
    protocol.call("status", {"settings_open": True})
    protocol.call("index", {"path": str(project)})
    assert wait_until(lambda: files_by_status().get("indexed") == 3, timeout=20)  # no reports: released


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
    assert power.parse_low_power("System-wide power settings:\n lowpowermode         1\n sleep                1")
    assert not power.parse_low_power(" lowpowermode         0\n powermode            0")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS frameworks")
def test_power_state_reads_the_system_without_pyobjc():
    state = power.read()  # IOKit + NSProcessInfo through ctypes; values depend on this Mac
    assert state.thermal in power.THERMAL_STATES and isinstance(state.on_battery, bool)
    assert "Foundation" not in sys.modules and "objc" not in sys.modules


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS QoS")
def test_thread_qos():
    seen = {}

    def body():
        seen["before"] = qos.thread_qos()
        qos.set_thread_qos("utility")
        seen["after"] = qos.thread_qos()

    t = threading.Thread(target=body)
    t.start()
    t.join()
    assert seen == {"before": "default", "after": "utility"}


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


def test_opened_result_is_recorded(start_daemon, project):
    start_daemon(mode="auto")
    protocol.call("index", {"path": str(project)})
    assert wait_until(lambda: files_by_status().get("indexed") == 3)
    r = protocol.call("search", {"query": "backoff_wrapper"})
    protocol.call("opened", {"event_id": r["event_id"], "chunk_id": r["results"][0]["chunk_id"]})
    conn = db.connect(Path(os.environ["LANTERN_HOME"]) / "index.db")
    assert conn.execute("SELECT opened_chunk_id FROM search_events WHERE id = ?", (r["event_id"],)).fetchone()[0]


def test_daemon_exits_when_its_parent_app_dies(sock, lantern_home, project):
    """Started by the menu bar app with LANTERN_EXIT_WITH_PARENT=1: no orphan after an app crash."""
    settings.save(settings.Settings(notify=False))
    env = {**os.environ, "LANTERN_EXIT_WITH_PARENT": "1", "LANTERN_EMBEDDER": "fake", "LANTERN_SOCKET": str(sock)}
    # A short-lived "app" process starts the daemon, waits until it serves, then dies.
    launcher = (
        "import os, subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-m', 'lantern.cli', 'daemon', 'run']); "
        "t = time.time()\n"
        "while not os.path.exists(os.environ['LANTERN_SOCKET']) and time.time() - t < 30: time.sleep(0.1)\n"
        "sys.exit(0 if os.path.exists(os.environ['LANTERN_SOCKET']) else 3)"
    )
    started = subprocess.run([sys.executable, "-c", launcher], env=env, timeout=60)
    assert started.returncode == 0, "daemon did not start"
    assert wait_until(lambda: not sock.exists(), timeout=30), "daemon kept running after its parent exited"


def test_daemon_stops_when_newer_code_upgraded_the_index(start_daemon, lantern_home):
    import sqlite3

    d = start_daemon(mode="ask")
    raw = sqlite3.connect(config.db_path())
    raw.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    raw.close()
    with pytest.raises(Exception, match="newer than this Lantern"):
        protocol.call("search", {"query": "x"})
    assert wait_until(lambda: d.stopping.is_set())  # the app's supervisor starts it again from the new code


def test_music_library_through_the_api(start_daemon):
    start_daemon(mode="ask")
    songs = [{"id": "A1", "title": "SKELETONS", "artist": "Travis Scott", "album": "ASTROWORLD", "track": 5}]
    r = protocol.call("music", {"songs": songs, "playlists": [{"id": "P1", "name": "Gym", "songs": ["A1"]}]})
    assert r == {"songs": 1, "playlists": 1, "embedded": 2}
    hit = protocol.call("search", {"query": "skeletons", "log": False})["music"][0]
    assert hit["title"] == "SKELETONS" and hit["playlist_name"] == "Gym"
    assert protocol.call("music", {"clear": True})["songs"] == 0
    assert protocol.call("search", {"query": "skeletons", "log": False})["music"] == []


def test_a_query_learns_the_row_usually_opened(start_daemon):
    start_daemon(mode="ask")
    key = "app|0|-1|/Applications/Slack.app|"
    for _ in range(2):
        event = protocol.call("search", {"query": "sl"})["event_id"]
        protocol.call("opened", {"event_id": event, "chunk_id": 0, "key": key})  # an opened search is never merged
    assert protocol.call("search", {"query": "SL", "log": False})["learned"] == key
    assert protocol.call("search", {"query": "s", "log": False})["learned"] == key  # a prefix learns from it too
    assert protocol.call("search", {"query": "slack", "log": False})["learned"] is None  # never chosen for this one


def test_one_letter_learns_from_the_longer_queries_typed_on(start_daemon):
    start_daemon(mode="ask")
    discord, docker = "app|0|-1|/Applications/Discord.app|", "app|0|-1|/Applications/Docker.app|"
    for query, key in [("di", discord), ("discord", discord), ("disc", discord), ("doc", docker)]:
        event = protocol.call("search", {"query": query})["event_id"]
        protocol.call("opened", {"event_id": event, "chunk_id": 0, "key": key})
    assert protocol.call("search", {"query": "d", "log": False})["learned"] == discord  # 3 of 4 choices
    assert protocol.call("search", {"query": "do", "log": False})["learned"] is None  # Docker only once


def test_import_reports_whether_alfred_is_there(start_daemon, monkeypatch, tmp_path):
    from lantern import importers

    monkeypatch.setattr(importers, "ALFRED_DB", tmp_path / "none.alfdb")
    start_daemon(mode="ask")
    assert protocol.call("import", {"source": "alfred", "check": True}) == {"available": False}
    assert protocol.call("import", {"source": "alfred"}) == {"imported": 0}


def test_daemon_stays_up_when_models_are_missing_and_recovers(sock, lantern_home):
    from lantern.embedding.mlx_backend import ModelNotDownloaded

    ready = {"ok": False}

    class DelayedEmbedder(FakeEmbedder):
        def load(self):
            if not ready["ok"]:
                raise ModelNotDownloaded("not downloaded yet")

    settings.save(settings.Settings(notify=False))
    d = Daemon(embedder=DelayedEmbedder(), power_reader=lambda: PowerState(on_battery=False))
    t = threading.Thread(target=d.run, daemon=True)
    t.start()
    try:
        assert wait_until(lambda: protocol.running())
        st = protocol.call("status")
        assert st["models_missing"] is True
        ans = protocol.call("search", {"query": "2+2", "quick": True})
        assert ans["answer"]["copy"] == "4"
        ready["ok"] = True
        proc = subprocess.Popen([sys.executable, "-c", "pass"], stdout=subprocess.PIPE, text=True)
        d._read_downloader(proc)
        st2 = protocol.call("status")
        assert st2["models_missing"] is False
    finally:
        d.stopping.set()
        t.join(timeout=5)


def test_make_transcriber_checks_whisper_available(monkeypatch):
    from lantern.service import worker
    from lantern.transcription import whisper

    monkeypatch.delenv("LANTERN_EMBEDDER", raising=False)
    monkeypatch.setattr(whisper, "available", lambda: False)
    assert worker.make_transcriber(True) is None
    monkeypatch.setattr(whisper, "available", lambda: True)
    assert isinstance(worker.make_transcriber(True), whisper.WhisperTranscriber)


def test_passage_returns_transcript_for_audio_and_frame_chunks(start_daemon):
    import numpy as np

    start_daemon(mode="ask")
    conn = db.connect(config.db_path())
    with db.transaction(conn):
        root_id = db.ensure_root(conn, Path("/tmp"))
        cur = conn.execute(
            "INSERT INTO files(root_id, path, size, mtime_ns, content_hash, kind, status) "
            "VALUES (?, ?, 1, 1, 'a', 'video', 'indexed')",
            (root_id, "/tmp/demo.mp4"),
        )
        file_id = cur.lastrowid
        mid = db.ensure_model(conn, FakeEmbedder().model_info())
        db.ensure_model(conn, FakeEmbedder().model_info(), images=True)
        z = np.zeros((1, 64), dtype=np.float32)
        frame = [db.ChunkRecord(0, "frame", None, start_ms=0, end_ms=1000)]
        audio = [db.ChunkRecord(0, "audio", None, start_ms=0, end_ms=30000)]
        tr = [db.ChunkRecord(0, "transcript", "Spotkanie o budżecie na czwartek.", start_ms=3000, end_ms=15000)]
        db.replace_file_chunks(conn, file_id, "demo.mp4", frame, z, mid, 7, {"frame"})
        db.replace_file_chunks(conn, file_id, "demo.mp4", audio, z, mid, 7, {"audio"})
        db.replace_file_chunks(conn, file_id, "demo.mp4", tr, z, mid, 7, {"transcript"})
    rows = conn.execute("SELECT id, kind FROM chunks WHERE file_id = ?", (file_id,)).fetchall()
    conn.close()
    by_kind = {r["kind"]: r["id"] for r in rows}
    assert "Spotkanie" in protocol.call("passage", {"chunk_id": by_kind["audio"]})["text"]
    assert "Spotkanie" in protocol.call("passage", {"chunk_id": by_kind["frame"]})["text"]


def test_a_file_the_worker_is_stuck_on_is_skipped(lantern_home, project, monkeypatch):
    """A worker stuck on one file (a hung PDFKit/Vision call) is killed after the time limit; that file's job fails
    at once with the reason, the other claimed jobs go back to the queue."""
    import subprocess as sp
    import time as tm

    from lantern.queue import jobs
    from lantern.scanner.diff import scan_root
    from lantern.service import core

    conn = db.connect(db.Path(os.environ["LANTERN_HOME"]) / "index.db")
    scan_root(conn, project)
    paths = [r[0] for r in conn.execute("SELECT path FROM files ORDER BY path")]
    jobs.claim(conn, "index", 10)  # all running, as a worker would have them
    conn.commit()
    monkeypatch.setattr(core, "STUCK_FILE_S", {"index": 1.0})
    d = Daemon(embedder=FakeEmbedder(), power_reader=lambda: PowerState(on_battery=False))
    d.worker = sp.Popen(["sleep", "60"])
    d.progress, d.progress_at = {"event": "progress", "stage": "index", "path": paths[0]}, tm.monotonic() - 5
    d._check_stuck_worker()
    assert d.worker is None
    rows = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT f.path, j.status, j.last_error FROM jobs j JOIN files f ON f.id = j.file_id")}  # fmt: skip
    assert rows[paths[0]][0] == "failed" and "took longer than" in rows[paths[0]][1]
    assert all(status == "queued" for p, (status, _) in rows.items() if p != paths[0])
    conn.close()


def test_indexing_pauses_when_the_disk_is_almost_full(lantern_home, monkeypatch):
    from lantern.service import core

    d = Daemon(embedder=FakeEmbedder(), power_reader=lambda: PowerState(on_battery=False))
    monkeypatch.setattr(core, "_free_bytes", lambda path: 500 * 1024**2)
    d._check_power()
    assert d._paused() == "low_disk" and any("2 GB free" in w for w in d.warnings)
    monkeypatch.setattr(core, "_free_bytes", lambda path: 50 * 1024**3)
    d._last_power_check = 0.0
    d._check_power()
    assert d._paused() is None


def test_transcripts_queued_while_transcription_is_off_do_not_wake_the_worker(lantern_home, project):
    from lantern.queue import jobs
    from lantern.scanner.diff import scan_root

    conn = db.connect(db.Path(os.environ["LANTERN_HOME"]) / "index.db")
    scan_root(conn, project)
    file_id = conn.execute("SELECT id FROM files LIMIT 1").fetchone()[0]
    conn.execute("DELETE FROM jobs")
    jobs.enqueue(conn, file_id, "transcribe")
    conn.commit()
    d = Daemon(embedder=FakeEmbedder(), power_reader=lambda: PowerState(on_battery=False),
               worker_env={"LANTERN_EMBEDDER": "fake"})  # fmt: skip
    d.settings.transcribe = False
    assert d._queued(conn) == {}  # waits, but is not work the worker can do now
    d.settings.transcribe = True
    assert d._queued(conn) == {"transcribe": 1}
    conn.close()


def test_dates_and_places_are_filled_in_the_background_and_follow_the_switch(lantern_home, tmp_path):
    from PIL import Image

    from lantern.scanner.diff import scan_root

    root = tmp_path / "zdjecia"
    root.mkdir()
    img = Image.new("RGB", (320, 240), (200, 30, 30))
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0x9003] = "2023:05:14 12:00:00"
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "N", (50.0, 3.0, 41.0), "E", (19.0, 56.0, 12.0)
    img.save(root / "a.jpg", exif=exif)
    conn = db.connect(db.Path(os.environ["LANTERN_HOME"]) / "index.db")
    scan_root(conn, root)
    conn.commit()
    d = Daemon(embedder=FakeEmbedder(), power_reader=lambda: PowerState(on_battery=False))
    d.manual_pause = True
    d._refresh_meta()  # paused like indexing
    assert conn.execute("SELECT meta_version FROM files").fetchone()[0] == 0
    d.manual_pause = False
    d._refresh_meta()
    assert conn.execute("SELECT date_source, place_id FROM files").fetchone()[:] == ("exif", 3094802)
    d.api_config_set("places", "false")
    assert conn.execute("SELECT place_id, lat FROM files").fetchone()[:] == (None, None)
    assert conn.execute("SELECT count(*) FROM places").fetchone()[0] == 0
    d.api_config_set("places", "true")
    d._refresh_meta()
    assert conn.execute("SELECT place_id FROM files").fetchone()[0] == 3094802
    conn.close()


def test_photos_indexed_before_get_their_people_counted_without_indexing_files_waiting_for_approval(
    lantern_home, tmp_path
):
    from PIL import Image

    from lantern.indexer import run_queue
    from lantern.scanner.diff import scan_root

    root = tmp_path / "zdjecia"
    root.mkdir()
    Image.new("RGB", (320, 240), (200, 30, 30)).save(root / "a.jpg")
    conn = db.connect(db.Path(os.environ["LANTERN_HOME"]) / "index.db")
    scan_root(conn, root)
    run_queue(conn, FakeEmbedder(), image_text=False)  # indexed by an older Lantern: people not counted
    (root / "nowy.txt").write_text("czeka na zgodę")
    scan_root(conn, root)  # a new file waiting for approval in "ask" mode
    conn.commit()
    d = Daemon(embedder=FakeEmbedder(), power_reader=lambda: PowerState(on_battery=False))
    started = []
    d._start_worker = lambda people_only=False: started.append(people_only)
    d._manage_worker()
    assert started == [True]  # the people-only worker, not the indexer
    d.settings.people = False
    d._people_check_at = 0.0
    started.clear()
    d._manage_worker()
    assert started == []
    conn.close()

"""Phase 17: decisions from this Mac's memory (service/machine.py) and the daemon's memory guard."""

import os

from lantern.embedding.fake import FakeEmbedder
from lantern.service import machine
from lantern.service import settings as settings_mod
from lantern.service.core import Daemon
from lantern.service.power import PowerState

GB = machine.GB


def mem(total=16, pressure=1, free=50, swap=0):
    return machine.Memory(total=total * GB, pressure=pressure, free_percent=free, swap_used=swap)


def test_the_real_mac_can_be_read():
    m = machine.read_system()
    assert m.total >= 4 * GB and m.pressure in (1, 2, 4) and 0 <= m.free_percent <= 100


def test_profiles_follow_the_amount_of_memory():
    assert machine.profile(8 * GB) == "light"
    assert machine.profile(16 * GB) == "full" and machine.profile(24 * GB) == "full"


def test_heavy_work_follows_the_live_state_not_the_size():
    assert machine.fits("whisper", mem(16, free=50))
    assert not machine.fits("whisper", mem(16, pressure=2, free=50))  # warn
    assert not machine.fits("whisper", mem(24, free=10))  # 24 GB but busy (training a model): 2.4 GB free
    assert not machine.fits("audio", mem(8, free=50))  # 4 GB free, 3.3 + 1.5 needed


def test_swap_counts_when_it_grows_not_its_size():
    w = machine.SwapWatch()
    assert not w.growing(mem(swap=5 * GB), now=0)  # old swap from yesterday
    assert not w.growing(mem(swap=5 * GB + 100 * 2**20), now=30)
    assert w.growing(mem(swap=5 * GB + 700 * 2**20), now=60)
    assert not w.growing(mem(swap=5 * GB + 700 * 2**20), now=300)  # steady again


def test_first_launch_defaults_follow_the_profile(lantern_home, monkeypatch):
    light = settings_mod.defaults_for("light")
    assert not light.transcribe and not light.index_video_frames and light.mode == "on_demand"
    full = settings_mod.defaults_for("full")
    assert full.transcribe and full.index_video_frames and full.mode == "ready"
    monkeypatch.setattr(machine, "read", lambda: mem(8))
    assert settings_mod.load().mode == "on_demand"  # no config.toml yet: this Mac's defaults
    settings_mod.save(settings_mod.Settings())
    assert settings_mod.load().mode == "ready"  # saved settings win


def test_the_daemon_pauses_on_memory_pressure_and_resumes_after_calm(lantern_home, monkeypatch):
    from lantern.service import core

    state = {"m": mem(swap=5 * GB)}  # gigabytes of old swap, as on a Mac in use
    d = Daemon(embedder=FakeEmbedder(), power_reader=lambda: PowerState(on_battery=False),
               memory_reader=lambda: state["m"])  # fmt: skip
    clock = {"t": 1000.0}
    monkeypatch.setattr(core.time, "monotonic", lambda: clock["t"])
    d._check_memory()
    assert d._paused() is None
    state["m"] = mem(pressure=2, swap=5 * GB)
    clock["t"] += 10
    d._check_memory()
    assert d._paused() is None  # "warn" alone: a busy Mac compressing idle memory, light indexing goes on
    state["m"] = mem(pressure=2, swap=6 * GB)  # and now swap grows fast: the Mac is thrashing
    clock["t"] += 10
    d._check_memory()
    assert d._paused() == "memory" and any("short of memory" in w for w in d.warnings)
    state["m"] = mem(swap=6 * GB)
    clock["t"] += 10
    d._check_memory()
    assert d._paused() == "memory"  # calm for 0 s: still waiting
    clock["t"] += core.MEMORY_CALM_S + 10
    d._check_memory()
    assert d._paused() is None


def test_critical_pressure_stops_the_worker_at_once(lantern_home):
    import subprocess

    state = {"m": mem(pressure=4)}
    d = Daemon(embedder=FakeEmbedder(), power_reader=lambda: PowerState(on_battery=False),
               memory_reader=lambda: state["m"])  # fmt: skip
    d.worker = subprocess.Popen(["sleep", "60"])
    d._check_memory()
    assert d.worker is None and d._paused() == "memory"


def test_transcripts_wait_while_the_mac_has_no_room_for_whisper(lantern_home, monkeypatch):
    monkeypatch.delenv("LANTERN_EMBEDDER", raising=False)
    d = Daemon(embedder=FakeEmbedder(), power_reader=lambda: PowerState(on_battery=False),
               memory_reader=lambda: mem(16, free=8))  # fmt: skip
    assert not d._whisper_fits()
    d.memory_reader = lambda: mem(16, free=60)
    assert d._whisper_fits()


def test_setup_lists_models_for_this_profile(lantern_home):
    d = Daemon(embedder=FakeEmbedder(), power_reader=lambda: PowerState(on_battery=False),
               memory_reader=lambda: mem(8))  # fmt: skip
    s = d.api_setup()
    assert s["profile"] == "light" and s["ram_gb"] == 8 and s["suggested_mode"] == "on_demand"
    rec = {m["key"] for m in s["models"] if m["recommended"]}
    assert rec == {"embeddinggemma"}
    whisper = next(m for m in s["models"] if m["key"] == "whisper")
    assert whisper["heavy"] and whisper["needs_gb"] == 1.8
    d.memory_reader = lambda: mem(16)
    assert all(m["recommended"] for m in d.api_setup()["models"])
    assert os.environ.get("LANTERN_HOME")


class _LoadableEmbedder(FakeEmbedder):
    def __init__(self):
        super().__init__()
        self.loaded, self.loads = True, 0

    def load(self):
        self.loaded, self.loads = True, self.loads + 1

    def unload(self):
        self.loaded = False


def test_on_demand_releases_the_search_model_when_idle_and_loads_it_back(lantern_home, monkeypatch):
    from lantern.service import core

    emb = _LoadableEmbedder()
    d = Daemon(embedder=emb, power_reader=lambda: PowerState(on_battery=False), memory_reader=lambda: mem())
    d.settings.mode = "ready"
    d.last_search_at -= core.MODEL_IDLE_S + 1
    d._release_idle_search_model()
    d.model_thread.submit(lambda: None).result()
    assert emb.loaded  # "always ready" keeps it
    d.settings.mode = "on_demand"
    d._release_idle_search_model()
    d.model_thread.submit(lambda: None).result()
    assert not emb.loaded
    d.api_search("umowa najmu", log=False)
    assert emb.loaded and emb.loads == 1 and d.api_status()["model_reload_ms"] is not None
    assert d.api_status()["search_mode"] == "on_demand"


def test_an_index_from_a_newer_lantern_is_explained_and_can_be_rebuilt(lantern_home, tmp_path):
    from lantern import config
    from lantern.store import db

    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "a.txt").write_text("umowa najmu")
    conn = db.connect(config.db_path())
    with db.transaction(conn):
        conn.execute("INSERT INTO roots(path) VALUES (?)", (str(folder),))
    conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    conn.close()
    d = Daemon(embedder=FakeEmbedder(), power_reader=lambda: PowerState(on_battery=False), memory_reader=lambda: mem())
    assert d._index_newer()
    s = d.api_status()
    assert s["index_problem"] == "newer" and s["files"] == {} and "pid" in s
    out = d.api_rebuild_index()
    assert out["roots"] == [str(folder)] and d.index_problem is None
    assert db.schema_version(config.db_path()) == db.SCHEMA_VERSION
    assert d.api_status().get("index_problem") is None


def test_a_damaged_index_is_detected(lantern_home):
    from lantern import config
    from lantern.store import db

    config.db_path().parent.mkdir(parents=True, exist_ok=True)
    config.db_path().write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)  # a header and garbage
    d = Daemon(embedder=FakeEmbedder(), power_reader=lambda: PowerState(on_battery=False), memory_reader=lambda: mem())
    assert d._index_damaged_at_start() or not db.healthy(config.db_path())
    d._check_index_health()
    assert d.index_problem == "damaged"
    d.api_rebuild_index()
    assert db.healthy(config.db_path()) and d.index_problem is None


def test_diagnostics_carry_no_file_names(lantern_home, tmp_path):
    from lantern import config
    from lantern.scanner.diff import scan_root
    from lantern.service import diagnostics
    from lantern.store import db

    folder = tmp_path / "Tajny projekt"
    folder.mkdir()
    (folder / "umowa-z-klientem.txt").write_text("poufne")
    conn = db.connect(config.db_path())
    scan_root(conn, folder)
    d = Daemon(embedder=FakeEmbedder(), power_reader=lambda: PowerState(on_battery=False), memory_reader=lambda: mem())
    d._warn(f"skipped a file that took longer than 30 min: {folder / 'umowa-z-klientem.txt'}")
    text = d.api_diagnostics()["text"]
    assert text.startswith("# Lantern diagnostics") and "- ram_gb:" in text and "- files_by_kind:" in text
    assert "Tajny" not in text and "umowa" not in text and "poufne" not in text and "<path>" in text
    assert diagnostics.report(None, conn).count("- running: no") == 1
    conn.close()


def test_warnings_carry_a_code_for_the_app_to_translate(lantern_home):
    state = {"m": mem(pressure=4)}
    d = Daemon(embedder=FakeEmbedder(), power_reader=lambda: PowerState(on_battery=False),
               memory_reader=lambda: state["m"])  # fmt: skip
    d._check_memory()
    s = d.api_status()
    assert s["warnings"][-1].endswith("indexing paused: the Mac is short of memory; it resumes by itself")  # CLI
    event = s["warning_events"][-1]
    assert event["code"] == "memory_pause" and event["args"] == {} and event["time"]

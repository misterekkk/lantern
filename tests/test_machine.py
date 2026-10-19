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
    m = machine.read()
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

    state = {"m": mem()}
    d = Daemon(embedder=FakeEmbedder(), power_reader=lambda: PowerState(on_battery=False),
               memory_reader=lambda: state["m"])  # fmt: skip
    clock = {"t": 1000.0}
    monkeypatch.setattr(core.time, "monotonic", lambda: clock["t"])
    d._check_memory()
    assert d._paused() is None
    state["m"] = mem(pressure=2)
    clock["t"] += 10
    d._check_memory()
    assert d._paused() == "memory" and any("short of memory" in w for w in d.warnings)
    state["m"] = mem()
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

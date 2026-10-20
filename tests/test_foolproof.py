"""Phase 17 fool-proofing: refused folders, estimates before adding a big one, a hand-edited config.toml."""

from pathlib import Path

import pytest
from conftest import write

from lantern.scanner import guard
from lantern.service import settings


@pytest.mark.parametrize("path", ["/", "/Users", "/Volumes", "/private/var", "/System/Volumes/Data"])
def test_whole_disk_is_refused(path):
    assert guard.refusal(Path(path)) == "disk"


@pytest.mark.parametrize("path", ["/System", "/System/Library/Fonts", "/Library", "/usr/local", "/Applications",
                                  "/private/etc"])  # fmt: skip
def test_system_folders_are_refused(path):
    assert guard.refusal(Path(path)) == "system"


def test_home_library_is_refused_but_cloud_folders_are_not():
    home = Path.home()
    assert guard.refusal(home / "Library") == "system"
    assert guard.refusal(home / "Library" / "Mail") == "system"
    assert guard.refusal(home / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Notes") is None
    assert guard.refusal(home / "Library" / "CloudStorage" / "Dropbox") is None
    assert guard.refusal(home) is None  # the home folder has its own policy (walk.py)
    assert guard.refusal(home / "Documents") is None


def test_lantern_data_folder_is_refused(lantern_home):
    assert guard.refusal(lantern_home.resolve()) == "lantern"
    assert guard.refusal((lantern_home / "logs").resolve()) == "lantern"


def test_estimate_counts_what_the_scanner_would_read(tmp_path, lantern_home):
    root = tmp_path / "docs"
    write(root, "a.md", "hello")
    write(root, "b.py", "def f(): pass")
    write(root, ".env", "SECRET=1")  # never counted: the scanner's own rules
    write(root, "node_modules/x.js", "x")
    est = guard.estimate(root.resolve())
    assert (est.files, est.complete, est.large, est.refused) == (2, True, False, None)
    assert est.by_kind == {"text": 1, "code": 1}


def test_estimate_marks_a_huge_folder_large(tmp_path, lantern_home, monkeypatch):
    root = tmp_path / "many"
    for i in range(12):
        write(root, f"{i}.md", "x")
    monkeypatch.setattr(guard, "LARGE_FILES", 10)
    assert guard.estimate(root.resolve()).large


def test_daemon_refuses_system_folders_and_estimates_new_ones(tmp_path, lantern_home):
    from lantern.embedding.fake import FakeEmbedder
    from lantern.service.core import Daemon

    d = Daemon(embedder=FakeEmbedder(), watch=False)
    with pytest.raises(ValueError, match="system folders"):
        d.api_index("/System/Library")
    write(tmp_path / "notes", "a.md", "hello")
    est = d.api_estimate(str(tmp_path / "notes"))
    assert (est["indexed"], est["files"], est["refused"]) == (False, 1, None)
    d.api_index(str(tmp_path / "notes"))
    assert d.api_estimate(str(tmp_path / "notes"))["indexed"]


def write_config(lantern_home, text):
    (lantern_home / "config.toml").parent.mkdir(parents=True, exist_ok=True)
    (lantern_home / "config.toml").write_text(text)


def test_broken_config_falls_back_to_defaults(lantern_home):
    write_config(lantern_home, "index_mode = auto\n[[[")  # not TOML
    s = settings.load()
    assert s.index_mode == "ask"
    assert "could not be read" in settings.problems[0]


def test_wrong_values_are_corrected_one_by_one(lantern_home):
    write_config(lantern_home, 'index_mode = "always"\ntranscribe = "yes"\ndebounce_s = 0\nnotify = false\n'
                               'exclude = ["~/secret", 5]\ncolour = "blue"\n')  # fmt: skip
    s = settings.load()
    assert (s.index_mode, s.transcribe, s.debounce_s, s.notify, s.exclude) == ("ask", True, 0.1, False, ["~/secret"])
    text = " | ".join(settings.problems)
    for key in ("index_mode", "transcribe", "debounce_s", "colour"):
        assert key in text
    settings.save(s)  # the corrected values are what gets written back
    assert settings.load() == s and settings.problems == []


def test_daemon_reports_corrected_settings(lantern_home):
    from lantern.embedding.fake import FakeEmbedder
    from lantern.service.core import Daemon

    write_config(lantern_home, "debounce_s = 9999\nnotify = false\n")
    d = Daemon(embedder=FakeEmbedder(), watch=False)
    assert d.settings.debounce_s == 60.0
    assert d.warning_events[0]["code"] == "settings_fixed"


def test_schedule_night_window_and_idle():
    s = settings.Settings(schedule="night", night_from=22, night_to=7)
    assert [settings.in_schedule(s, h, 0) for h in (21, 22, 3, 7)] == [False, True, True, False]
    s = settings.Settings(schedule="night", night_from=1, night_to=5)
    assert [settings.in_schedule(s, h, 0) for h in (0, 1, 4, 5)] == [False, True, True, False]
    s = settings.Settings(schedule="idle")
    assert not settings.in_schedule(s, 12, 60) and settings.in_schedule(s, 12, 301)
    assert settings.in_schedule(settings.Settings(), 12, 0)


def test_daemon_pauses_outside_the_schedule(lantern_home):
    from lantern.embedding.fake import FakeEmbedder
    from lantern.service.core import Daemon
    from lantern.service.power import PowerState

    settings.save(settings.Settings(schedule="idle", notify=False))
    state = PowerState(idle_s=10)
    d = Daemon(embedder=FakeEmbedder(), watch=False, power_reader=lambda: state)
    d._check_power()
    assert d._paused() == "schedule"
    state = PowerState(idle_s=600)
    d._last_power_check = 0.0
    d._check_power()
    assert d._paused() is None


def test_restore_defaults_keeps_folders_and_exclusions(tmp_path, lantern_home):
    from lantern.embedding.fake import FakeEmbedder
    from lantern.service.core import Daemon

    settings.save(settings.Settings(index_mode="auto", notify=False, schedule="night", exclude=["~/private"],
                                    exclude_extensions=[".log"]))  # fmt: skip
    d = Daemon(embedder=FakeEmbedder(), watch=False)
    write(tmp_path / "notes", "a.md", "hello")
    d.api_index(str(tmp_path / "notes"))
    out = d.api_restore_defaults()
    s = settings.load()
    assert (s.index_mode, s.notify, s.schedule, s.exclude) == ("ask", True, "anytime", ["~/private"])
    assert s.exclude_extensions == settings.Settings().exclude_extensions
    assert {"index_mode", "schedule", "exclude_extensions"} <= set(out["changed"])
    assert d.api_estimate(str(tmp_path / "notes"))["indexed"]  # the folder stays


def test_delete_all_data_leaves_nothing_behind(tmp_path, lantern_home):
    from lantern import config
    from lantern.embedding.fake import FakeEmbedder
    from lantern.service.core import Daemon, logs_dir

    settings.save(settings.Settings(index_mode="auto", notify=False))
    d = Daemon(embedder=FakeEmbedder(), watch=False)
    write(tmp_path / "notes", "a.md", "the secret plan")
    d.api_index(str(tmp_path / "notes"))
    logs_dir().mkdir(parents=True, exist_ok=True)
    (logs_dir() / "daemon.log").write_text("indexed the secret plan")
    assert d.api_delete_all_data() == {"deleted": True, "roots": 1}
    assert not settings.path().exists() and not logs_dir().exists()
    raw = config.db_path().read_bytes()
    assert b"secret plan" not in raw
    conn = d._conn()
    assert conn.execute("SELECT count(*) FROM roots").fetchone()[0] == 0
    conn.close()
    assert (tmp_path / "notes" / "a.md").read_text() == "the secret plan"  # the user's files are never touched


def test_battery_mode_releases_the_model_only_on_battery(lantern_home):
    from lantern.embedding.fake import FakeEmbedder
    from lantern.service.core import Daemon
    from lantern.service.power import PowerState

    settings.save(settings.Settings(mode="battery", notify=False))
    state = PowerState(on_battery=False)
    d = Daemon(embedder=FakeEmbedder(), watch=False, power_reader=lambda: state)
    d._check_power()
    assert not d._release_when_idle()
    state = PowerState(on_battery=True)
    d._last_power_check = 0.0
    d._check_power()
    assert d._release_when_idle()

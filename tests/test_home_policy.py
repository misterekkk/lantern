"""Whole-computer indexing: home-folder policy, macOS bundles, hidden folders, iCloud placeholders, permissions."""

from pathlib import Path

from conftest import write

from lantern.indexer import run_queue
from lantern.scanner import walk
from lantern.scanner.diff import scan_root
from lantern.scanner.walk import WalkReport, iter_files


def rel_paths(root, report=None):
    return {e.path.relative_to(root).as_posix() for e in iter_files(root, report)}


def test_home_policy_skips_library_but_keeps_icloud_drive(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    write(home, "Documents/plan.md", "plan\n")
    write(home, "Desktop/notes.txt", "notes\n")
    write(home, "Library/Mail/V10/message.txt", "private mail\n")
    write(home, "Library/Caches/x.json", "{}\n")
    write(home, "Library/Mobile Documents/com~apple~CloudDocs/Studia/notatki.md", "notatki\n")
    write(home, "Library/Mobile Documents/iCloud~com~app/Documents/data.json", "{}\n")  # app container: skipped
    write(home, "Applications/Tool.app/Contents/Info.plist", "x\n")
    write(home, "Music/Music/Media/song.mp3", "x\n")
    files = {e.path for e in iter_files(home)}
    assert files == {
        home / "Documents/plan.md",
        home / "Desktop/notes.txt",
        home / "Library/Mobile Documents/com~apple~CloudDocs/Studia/notatki.md",
    }


def test_bundles_and_hidden_folders_are_skipped(tmp_path):
    root = tmp_path / "r"
    write(root, "readme.md", "x\n")
    write(root, "Photos Library.photoslibrary/database/Photos.sqlite.txt", "x\n")
    write(root, "Some.app/Contents/Resources/help.txt", "x\n")
    write(root, "Raport.pages/Index/Document.txt", "x\n")
    write(root, ".ssh/config.txt", "Host x\n")
    write(root, ".config/tool/settings.yaml", "a: 1\n")
    write(root, ".github/workflows/ci.yml", "on: push\n")
    write(root, ".hidden_notes.md", "x\n")
    assert rel_paths(root) == {"readme.md", ".github/workflows/ci.yml"}


def test_icloud_placeholders_are_skipped_and_their_entries_kept(conn, project, embedder, monkeypatch):
    scan_root(conn, project)
    run_queue(conn, embedder)
    evicted = str(project / "README.md")
    real = walk.is_dataless
    monkeypatch.setattr(walk, "is_dataless", lambda st: st.st_ino == Path(evicted).stat().st_ino or real(st))
    report = WalkReport()
    assert "README.md" not in rel_paths(project, report) and report.cloud_only == 1

    res = scan_root(conn, project)  # iCloud evicted the file to free space: it still exists, keep it searchable
    assert not res.deleted and res.cloud_only == 1
    assert conn.execute("SELECT COUNT(*) FROM files WHERE path = ?", (evicted,)).fetchone()[0] == 1


def test_unreadable_folder_keeps_its_entries(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    locked = project / "src"
    locked.chmod(0o000)  # like ~/Documents before macOS grants access
    try:
        res = scan_root(conn, project)
    finally:
        locked.chmod(0o755)
    assert not res.deleted and any(p.endswith("/src") for p in res.unreadable)
    assert conn.execute("SELECT COUNT(*) FROM files WHERE path LIKE '%net.py'").fetchone()[0] == 1


def test_photos_are_indexed_unless_disabled(tmp_path):
    root = tmp_path / "r"
    for name in ("a.jpg", "b.HEIC", "c.png"):
        write(root, name, "x\n")
    assert rel_paths(root) == {"a.jpg", "b.HEIC", "c.png"}
    report = WalkReport()
    assert {e.path.name for e in iter_files(root, report, include_images=False)} == set() and report.images == 3


def test_dry_run_reads_and_writes_nothing(project, lantern_home):
    import os
    import subprocess
    import sys

    env = {**os.environ, "LANTERN_HOME": str(lantern_home), "LANTERN_SOCKET": str(lantern_home / "none.sock")}
    out = subprocess.run([sys.executable, "-m", "lantern.cli", "index", str(project), "--dry-run"],
                         capture_output=True, text=True, env=env)  # fmt: skip
    assert out.returncode == 0 and "code" in out.stdout and "text" in out.stdout
    assert not (lantern_home / "index.db").exists()


def test_user_excludes_paths_and_patterns(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    root = home / "Downloads"
    write(root, "commons-math3-3.6.1/src/Foo.java", "class Foo {}\n")
    write(root, "dane/rekord1.json", "{}\n")
    write(root, "notatka.md", "x\n")
    excludes = ["~/Downloads/commons-math3-3.6.1", "dane/*.json"]
    assert {e.path.relative_to(root).as_posix() for e in iter_files(root, excludes=excludes)} == {"notatka.md"}


def test_excluding_indexed_files_removes_them_at_next_scan(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    res = scan_root(conn, project, excludes=[str(project / "notes")])
    assert len(res.deleted) == 1 and res.deleted[0].endswith("łódź.txt")


def test_exclude_setting_roundtrip(lantern_home):
    from lantern.service import settings

    s = settings.load()
    settings.add_exclude(s, '~/Downloads/dziwna "nazwa"')
    settings.add_exclude(s, "*.log")
    settings.save(s)
    assert settings.load().exclude == ['~/Downloads/dziwna "nazwa"', "*.log"]
    settings.remove_exclude(s, "*.log")
    settings.save(s)
    assert settings.load().exclude == ['~/Downloads/dziwna "nazwa"']

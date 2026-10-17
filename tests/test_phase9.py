"""Phase 9 (daemon side): ignored extensions, audio/video switch, folder tree, performance monitor, text preview."""

import os
from pathlib import Path

import pytest
from conftest import write

from lantern.config import MASS_DELETE_MIN_FILES
from lantern.embedding.fake import FakeEmbedder
from lantern.indexer import run_queue
from lantern.scanner.diff import MassDeletionRefused, scan_root
from lantern.scanner.walk import iter_files, normalize_extension
from lantern.service import settings as settings_mod
from lantern.service import tree as tree_mod
from lantern.service.perf import PerfMonitor, footprint, worker_models
from lantern.service.power import PowerState


def names(root, **kw):
    return {e.path.relative_to(root).as_posix() for e in iter_files(root, **kw)}


def test_extension_forms_are_normalised():
    assert normalize_extension("csv") == normalize_extension(".CSV") == normalize_extension("*.csv") == ".csv"
    assert normalize_extension(" tar.gz ") == ".tar.gz"
    for bad in ("", "*", ".", "a b", "../x", "c/sv"):
        assert normalize_extension(bad) is None, bad


def test_default_ignored_extensions():
    assert settings_mod.Settings().exclude_extensions == [".ipynb", ".csv", ".tsv"]


def test_ignored_extensions_and_media_switch(tmp_path):
    root = tmp_path / "r"
    for name in ("a.py", "b.csv", "c.ipynb", "d.json", "song.mp3", "film.mp4", "pic.png"):
        write(root, name, "x\n")
    assert names(root) == {"a.py", "b.csv", "c.ipynb", "d.json", "song.mp3", "film.mp4", "pic.png"}
    assert names(root, skip_extensions=["csv", "*.json"]) == {"a.py", "c.ipynb", "song.mp3", "film.mp4", "pic.png"}
    assert names(root, include_media=False) == {"a.py", "b.csv", "c.ipynb", "d.json", "pic.png"}


def test_settings_extension_list(lantern_home):
    s = settings_mod.Settings(exclude_extensions=[])
    assert settings_mod.add_extension(s, "*.LOG") == ".log" and s.exclude_extensions == [".log"]
    settings_mod.add_extension(s, "log")  # no duplicates
    assert s.exclude_extensions == [".log"]
    with pytest.raises(ValueError):
        settings_mod.add_extension(s, "a b")
    settings_mod.save(s)
    assert settings_mod.load().exclude_extensions == [".log"]
    settings_mod.remove_extension(s, ".log")
    assert s.exclude_extensions == []


def test_excluding_a_big_folder_is_not_a_mass_deletion(conn, tmp_path, embedder):
    root = tmp_path / "r"
    for i in range(MASS_DELETE_MIN_FILES + 5):
        write(root, f"big/f{i}.txt", f"note {i}\n")
    write(root, "keep.txt", "kept\n")
    scan_root(conn, root)
    result = scan_root(conn, root, excludes=[str(root / "big")])  # deliberate: files are still on the disk
    assert len(result.deleted) == MASS_DELETE_MIN_FILES + 5
    scan_root(conn, root)  # included again
    import shutil

    shutil.rmtree(root / "big")  # vanished (e.g. wrong folder, unmounted): the guard still protects
    with pytest.raises(MassDeletionRefused):
        scan_root(conn, root)


@pytest.fixture
def tree_root(tmp_path):
    root = tmp_path / "Projekty"
    write(root, "app/main.py", "def main():\n    pass\n")
    write(root, "app/.env", "SECRET=1\n")
    write(root, "app/build/out.py", "x = 1\n")
    write(root, "app/logs/run.log", "log\n")
    write(root, "app/.gitignore", "logs/\n")
    write(root, "app/data.csv", "a,b\n1,2\n")
    write(root, "docs/notes.md", "# Notatki\n")
    write(root, "docs/old/draft.md", "# Szkic\n")
    write(root, "tool.app/Contents/x.txt", "bundle\n")
    return root


def test_tree_states_and_reasons(conn, tree_root, embedder):
    s = settings_mod.Settings(exclude=[str(tree_root / "docs" / "old")])
    scan_root(conn, tree_root, **settings_mod.walk_options(s))
    run_queue(conn, embedder)
    roots = [str(tree_root)]

    top = {e["name"]: e for e in tree_mod.list_children(conn, str(tree_root), roots, s)["entries"]}
    assert top["app"]["state"] == "included" and top["app"]["indexed"] == 1
    assert top["docs"]["state"] == "partial"  # docs/old is excluded inside
    assert top["tool.app"]["state"] == "skipped" and top["tool.app"]["reason"] == "bundle"
    assert list(top)[:2] == ["app", "docs"]  # folders first

    app = {e["name"]: e for e in tree_mod.list_children(conn, str(tree_root / "app"), roots, s)["entries"]}
    assert app["main.py"]["state"] == "included" and app["main.py"]["indexed"] == 1
    assert (app[".env"]["reason"], app["build"]["reason"], app["logs"]["reason"]) == ("hidden", "build", "gitignore")
    assert app["data.csv"]["reason"] == "extension"  # default ignored extension

    docs = {e["name"]: e for e in tree_mod.list_children(conn, str(tree_root / "docs"), roots, s)["entries"]}
    assert docs["old"]["state"] == "excluded"
    with pytest.raises(ValueError):
        tree_mod.list_children(conn, str(tree_root.parent), roots, s)  # outside the indexed folders


def test_unchecking_and_checking(conn, tree_root, embedder):
    s = settings_mod.Settings(exclude=["*.log"])
    scan_root(conn, tree_root, **settings_mod.walk_options(s))
    run_queue(conn, embedder)
    assert tree_mod.removal_preview(conn, str(tree_root / "docs")) == 2

    tree_mod.set_included(s, str(tree_root / "docs" / "old"), False)
    tree_mod.set_included(s, str(tree_root / "docs"), False)  # covers docs/old: the nested entry is dropped
    assert s.exclude == ["*.log", str(tree_root / "docs")]

    tree_mod.set_included(s, str(tree_root / "docs" / "old" / "draft.md"), True)  # one file back from inside
    assert s.exclude == ["*.log", str(tree_root / "docs" / "notes.md")]
    found = names(tree_root, **settings_mod.walk_options(s))
    assert "docs/old/draft.md" in found and "docs/notes.md" not in found

    tree_mod.set_included(s, str(tree_root / "docs"), True)
    assert s.exclude == ["*.log"]


def test_footprint_peak_survives_a_spike():
    import mmap

    before = footprint(os.getpid())
    with mmap.mmap(-1, 64 * 2**20) as spike:
        for off in range(0, 64 * 2**20, 4096):
            spike[off] = 1
    after = footprint(os.getpid())
    assert before and after and after[1] >= before[0] + 60 and after[0] < after[1]


def test_worker_models_from_progress():
    assert worker_models({"stage": "transcribe", "path": "/a/x.mp3"}, True) == ["whisper"]
    assert worker_models({"stage": "index", "path": "/a/x.mov"}, True) == [
        "embeddinggemma:audio", "embeddinggemma:vision", "siglip_image"]  # fmt: skip
    assert worker_models({"stage": "index", "path": "/a/x.mov"}, False) == ["embeddinggemma:audio"]
    assert worker_models({"stage": "index", "path": "/a/x.HEIC"}, True) == ["embeddinggemma:vision", "siglip_image"]
    assert worker_models({"stage": "index", "path": "/a/x.py"}, True) == ["embeddinggemma:text"]


def test_perf_snapshot_has_history_and_models():
    perf = PerfMonitor()
    perf.tick(None, {})
    snap = perf.snapshot({}, ["embeddinggemma"], True)
    assert len(snap["samples"]) == 1 and snap["daemon"]["mb"] > 0 and snap["worker"]["running"] is False
    keys = {m["key"]: m for m in snap["models"]}
    assert set(keys) == {"embeddinggemma", "whisper", "siglip_image", "siglip_text"}
    assert keys["embeddinggemma"]["loaded_daemon"] and not keys["siglip_text"]["loaded_daemon"]


def test_daemon_api_tree_extensions_preview(lantern_home, tree_root):
    from lantern.service.core import Daemon

    d = Daemon(embedder=FakeEmbedder(), watch=False, power_reader=lambda: PowerState())
    d.scan(str(tree_root))
    conn = d._conn()
    run_queue(conn, d.embedder)
    conn.close()

    assert {e["name"] for e in d.api_tree(str(tree_root))["entries"]} >= {"app", "docs"}
    assert d.api_tree_preview(str(tree_root / "docs"))["indexed"] == 2
    sugg = {s["ext"]: s for s in d.api_extension_suggestions()["suggestions"]}
    assert sugg[".md"]["indexed"] == 2 and sugg[".py"]["kind"] == "code" and ".heic" in sugg

    preview = d.api_preview(str(tree_root / "app" / "main.py"), 2, 2, context=1)
    assert preview["lines"] == [[1, "def main():"], [2, "    pass"], [3, ""]]
    with pytest.raises(ValueError):
        d.api_preview(str(tree_root / "app" / ".env"), 1)  # not indexed: never read through the API
    assert Path(settings_mod.path()).parent == lantern_home


def test_tree_search_finds_names_also_inside_excluded_folders(conn, tree_root, embedder):
    s = settings_mod.Settings(exclude=[str(tree_root / "docs")])
    scan_root(conn, tree_root, **settings_mod.walk_options(s))
    roots = [str(tree_root)]
    hits = {e["name"]: e for e in tree_mod.search(conn, "DRAFT", roots, s)["entries"]}
    assert hits["draft.md"]["state"] == "excluded"  # inside the excluded docs/ folder: can be checked again
    assert [e["name"] for e in tree_mod.search(conn, "out", roots, s)["entries"]] == []  # build/ is never walked
    assert {e["name"] for e in tree_mod.search(conn, "ma", roots, s)["entries"]} >= {"main.py"}
    write(tree_root, "docs/zażółć.md", "x\n")
    assert [e["name"] for e in tree_mod.search(conn, "zazolc", roots, s)["entries"]] == ["zażółć.md"]  # accents

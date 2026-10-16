"""A messy real disk: the scan and the queue must finish and skip what they cannot read, never hang or crash.

Symlink loops, a truncated recording, a huge one-line log, a file without read permission, secrets without a dot,
dependency folders and a .gitignore above the indexed folder.
"""

import os
import subprocess
import time

import pytest
from conftest import write

from lantern.embedding.fake import FakeEmbedder
from lantern.extractors.media import FFmpegMissing
from lantern.extractors.media import find_ffmpeg as _find_ffmpeg
from lantern.indexer import run_queue
from lantern.scanner.diff import scan_root
from lantern.scanner.walk import iter_files


def find_ffmpeg() -> str | None:
    try:
        return _find_ffmpeg()
    except FFmpegMissing:
        return None


def test_messy_folder_is_indexed_without_hanging(conn, tmp_path):
    root = tmp_path / "chaos"
    write(root, "notes.md", "# Notes\n\nA normal file among the mess.\n")
    write(root, "logs/huge.log", "x" * (3 * 1024 * 1024))  # 3 MB without a newline: over the text limit
    write(root, "locked.txt", "unreadable\n")
    os.chmod(root / "locked.txt", 0)
    (root / "loop").mkdir()
    os.symlink("..", root / "loop" / "up")  # a folder that points at its parent
    os.symlink(root / "loop", root / "loop" / "self")
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        clip = root / "recording.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-c:a", "aac",
                        str(clip)], check=True)  # fmt: skip
        data = clip.read_bytes()
        clip.write_bytes(data[: len(data) // 3])  # cut off like an interrupted recording
    try:
        t0 = time.monotonic()
        scan_root(conn, root)
        stats = run_queue(conn, FakeEmbedder())
        assert time.monotonic() - t0 < 60
    finally:
        os.chmod(root / "locked.txt", 0o644)
    paths = {r[0].rsplit("/", 1)[-1]: r[1] for r in conn.execute("SELECT path, status FROM files")}
    assert paths["notes.md"] == "indexed"
    assert paths.get("huge.log") in (None, "skipped") and paths.get("locked.txt") in (None, "skipped", "failed")
    assert not [p for p in paths if p in ("up", "self")]  # links are never followed
    assert stats.indexed >= 1
    if ffmpeg:  # the cut recording either yields what could be decoded or fails alone; the queue went on
        assert paths["recording.mp4"] in ("indexed", "failed", "skipped")


def test_dependency_folders_and_secrets_without_a_dot_are_skipped(tmp_path):
    root = tmp_path / "proj"
    write(root, "main.go", "package main\n")
    write(root, "vendor/github.com/x/y.go", "package y\n")
    write(root, "keys/service-account-prod.json", "{}\n")
    write(root, "gcp-credentials.json", "{}\n")
    write(root, "id_rsa", "-----BEGIN OPENSSH PRIVATE KEY-----\n")
    names = {e.path.name for e in iter_files(root)}
    assert names == {"main.go"}


@pytest.mark.parametrize("indexed", ["src", "src/deep"])
def test_gitignore_above_the_indexed_folder_applies_inside_the_repository(tmp_path, indexed):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    write(repo, ".gitignore", "generated/\n*.tmp\n")
    write(repo, "src/deep/app.py", "print(1)\n")
    write(repo, "src/deep/generated/schema.py", "X = 1\n")
    write(repo, "src/deep/scratch.tmp", "temp\n")
    names = {e.path.name for e in iter_files(repo / indexed)}
    assert names == {"app.py"}


def test_gitignore_outside_any_repository_is_not_inherited(tmp_path):
    write(tmp_path, ".gitignore", "*.md\n")  # e.g. a stray file in the home folder
    write(tmp_path, "docs/readme.md", "# Hi\n")
    assert {e.path.name for e in iter_files(tmp_path / "docs")} == {"readme.md"}

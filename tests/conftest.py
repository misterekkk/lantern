import os
from pathlib import Path

import pytest

from lantern.embedding.fake import FakeEmbedder
from lantern.store import db


@pytest.fixture(autouse=True)
def lantern_home(tmp_path, monkeypatch):
    """Never touch the real index in ~/Library during tests."""
    home = tmp_path / "lantern-home"
    monkeypatch.setenv("LANTERN_HOME", str(home))
    monkeypatch.setenv("LANTERN_APP_DIRS", "")  # no applications unless a test makes some (never the real ones)
    monkeypatch.setenv("LANTERN_SETTINGS_DIR", "")  # nor System Settings
    monkeypatch.setenv("LANTERN_NOTIFY", "0")  # never a real macOS notification (a test daemon has notify on)
    return home


@pytest.fixture(autouse=True)
def calm_machine(monkeypatch):
    """A Mac with room to spare: the real memory pressure of the machine running the tests must not pause daemons
    (a busy Mac made daemon tests fail at random). Tests of the memory guard pass their own readers."""
    from lantern.service import machine

    monkeypatch.setattr(machine, "read", lambda: machine.Memory(total=16 * machine.GB, pressure=1, free_percent=50,
                                                                swap_used=0))  # fmt: skip


@pytest.fixture
def conn(lantern_home):
    c = db.connect(lantern_home / "index.db")
    yield c
    c.close()


@pytest.fixture
def embedder():
    return FakeEmbedder()


def write(root: Path, rel: str, content: str, mtime_offset: int = 0) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mtime_offset:
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + mtime_offset * 1_000_000_000))
    return path


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    write(root, "README.md", "# Drzewa decyzyjne\n\nPrzycinanie drzewa decyzyjnego i dobór głębokości.\n")
    write(root, "src/net.py", "def backoff_wrapper(fn, retries=3):\n    return fn()\n\n\nclass Client:\n    pass\n")
    write(root, "notes/łódź.txt", "Spotkanie w Łodzi w czwartek o dziesiątej.\n")
    return root

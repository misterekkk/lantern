import shutil

import pytest
from conftest import write

from lantern.indexer import run_queue
from lantern.scanner.diff import MassDeletionRefused, RootUnavailable, scan_root


def file_count(conn):
    return conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]


def test_unavailable_root_removes_nothing(conn, project, embedder, tmp_path):
    scan_root(conn, project)
    run_queue(conn, embedder)
    mounted = tmp_path / "unplugged"
    project.rename(mounted)  # simulates an unmounted disk / network share
    with pytest.raises(RootUnavailable):
        scan_root(conn, project)
    assert file_count(conn) == 3

    mounted.rename(project)  # plugged back in: normal diff, nothing lost
    res = scan_root(conn, project)
    assert res.unchanged == 3 and not res.deleted


def test_unreadable_root_removes_nothing(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    project.chmod(0o000)
    try:
        with pytest.raises(RootUnavailable):
            scan_root(conn, project)
    finally:
        project.chmod(0o755)
    assert file_count(conn) == 3


def test_mass_deletion_requires_force(conn, tmp_path, embedder):
    root = tmp_path / "big"
    for i in range(30):
        write(root, f"doc{i}.md", f"dokument {i}\n")
    scan_root(conn, root)
    run_queue(conn, embedder)
    for i in range(25):
        (root / f"doc{i}.md").unlink()
    with pytest.raises(MassDeletionRefused):
        scan_root(conn, root)
    assert file_count(conn) == 30
    res = scan_root(conn, root, force=True)
    assert len(res.deleted) == 25 and file_count(conn) == 5


def test_small_deletions_do_not_need_force(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    shutil.rmtree(project / "notes")
    res = scan_root(conn, project)
    assert len(res.deleted) == 1

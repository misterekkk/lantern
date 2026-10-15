import os

from conftest import write

from lantern.indexer import run_queue
from lantern.scanner.diff import scan_root
from lantern.scanner.walk import iter_files


def paths(root):
    return {e.path.relative_to(root).as_posix() for e in iter_files(root)}


def queued(conn):
    return conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'queued'").fetchone()[0]


def test_new_files_are_enqueued(conn, project):
    res = scan_root(conn, project)
    assert len(res.new) == 3 and queued(conn) == 3


def test_unchanged_files_are_skipped(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    res = scan_root(conn, project)
    assert res.unchanged == 3 and not res.new and not res.changed and queued(conn) == 0


def test_changed_content_is_reindexed(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    write(project, "README.md", "# Nowa treść\n", mtime_offset=5)
    res = scan_root(conn, project)
    assert [p.endswith("README.md") for p in res.changed] == [True] and queued(conn) == 1


def test_touched_file_with_same_content_is_not_reindexed(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    readme = project / "README.md"
    st = readme.stat()
    os.utime(readme, ns=(st.st_atime_ns, st.st_mtime_ns + 10**10))
    res = scan_root(conn, project)
    assert len(res.touched) == 1 and not res.changed and queued(conn) == 0


def test_deleted_file_is_removed_with_chunks(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    (project / "src" / "net.py").unlink()
    res = scan_root(conn, project)
    assert len(res.deleted) == 1
    assert conn.execute("SELECT COUNT(*) FROM files WHERE path LIKE '%net.py'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'backoff_wrapper'").fetchone()[0] == 0


def test_moved_file_keeps_vectors_without_reembedding(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    chunks_before = conn.execute("SELECT id FROM chunks ORDER BY id").fetchall()
    calls = embedder.texts_embedded
    (project / "src" / "net.py").rename(project / "src" / "network.py")
    res = scan_root(conn, project)
    run_queue(conn, embedder)
    assert len(res.moved) == 1 and not res.new and not res.deleted
    assert embedder.texts_embedded == calls
    assert conn.execute("SELECT id FROM chunks ORDER BY id").fetchall() == chunks_before
    hit = conn.execute("SELECT path FROM chunks_fts WHERE chunks_fts MATCH 'path:network'").fetchone()
    assert hit is not None


def test_default_exclusions_and_secrets(tmp_path):
    root = tmp_path / "r"
    write(root, "app.py", "x = 1\n")
    write(root, ".env", "API_KEY=secret\n")
    write(root, ".env.local", "API_KEY=secret\n")
    write(root, "certs/server.pem", "-----BEGIN PRIVATE KEY-----\n")
    write(root, "config/secrets.yaml", "token: abc\n")
    write(root, "node_modules/lib/index.js", "module.exports = 1\n")
    write(root, ".git/config", "[core]\n")
    write(root, "image.png", "not really\n")
    assert paths(root) == {"app.py"}


def test_gitignore_is_respected_including_nested(tmp_path):
    root = tmp_path / "r"
    write(root, ".gitignore", "*.log\ngenerated/\n")
    write(root, "keep.md", "a\n")
    write(root, "debug.log", "a\n")
    write(root, "generated/out.py", "a = 1\n")
    write(root, "pkg/.gitignore", "local_*.py\n")
    write(root, "pkg/local_tmp.py", "a = 1\n")
    write(root, "pkg/real.py", "a = 1\n")
    write(root, "other/local_x.py", "a = 1\n")  # nested rule must not leak to siblings
    assert paths(root) == {"keep.md", "pkg/real.py", "other/local_x.py"}


def test_symlinks_are_not_followed(tmp_path):
    outside = tmp_path / "outside"
    write(outside, "secret_notes.md", "outside the root\n")
    root = tmp_path / "r"
    write(root, "a.md", "inside\n")
    (root / "link_dir").symlink_to(outside, target_is_directory=True)
    (root / "link_file.md").symlink_to(outside / "secret_notes.md")
    assert paths(root) == {"a.md"}


def test_lockfiles_are_excluded(tmp_path):
    root = tmp_path / "r"
    write(root, "package.json", '{"name": "app"}\n')
    write(root, "pnpm-lock.yaml", "lockfileVersion: 9\n")
    write(root, "uv.lock", "version = 1\n")
    assert paths(root) == {"package.json"}

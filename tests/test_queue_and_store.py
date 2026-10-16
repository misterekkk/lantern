from pathlib import Path

import numpy as np
from conftest import write

from lantern.embedding.fake import FakeEmbedder
from lantern.indexer import run_queue
from lantern.queue import jobs
from lantern.scanner.diff import scan_root
from lantern.store import db


def test_enqueue_is_idempotent(conn, project):
    scan_root(conn, project)
    file_id = conn.execute("SELECT id FROM files LIMIT 1").fetchone()[0]
    for _ in range(3):
        jobs.enqueue(conn, file_id, "index")
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE file_id = ?", (file_id,)).fetchone()[0] == 1


def test_running_jobs_are_recovered_after_crash(conn, project, embedder):
    scan_root(conn, project)
    with db.transaction(conn):
        jobs.claim(conn, "index", 10)  # process "crashes" here
    assert jobs.counts(conn) == {"running": 3}
    stats = run_queue(conn, embedder)
    assert stats.indexed == 3 and jobs.counts(conn) == {}


class FlakyEmbedder(FakeEmbedder):
    """Fails for any text containing 'BOOM'."""

    def embed_text(self, texts):
        if any("BOOM" in t for t in texts):
            raise RuntimeError("simulated backend failure")
        return super().embed_text(texts)


def test_one_failing_file_does_not_block_others(conn, project):
    write(project, "bad.md", "BOOM\n")
    scan_root(conn, project)
    emb = FlakyEmbedder()
    stats = run_queue(conn, emb)
    assert stats.indexed == 3 and stats.failed >= 1
    # retried up to the limit, then marked as error
    status, error = conn.execute("SELECT status, error FROM files WHERE path LIKE '%bad.md'").fetchone()
    assert status == "error" and "simulated" in error
    assert tuple(conn.execute("SELECT attempts, status FROM jobs").fetchone()) == (jobs.MAX_ATTEMPTS, "failed")


def test_binary_file_is_skipped(conn, tmp_path, embedder):
    root = tmp_path / "r"
    root.mkdir()
    (root / "data.json").write_bytes(b"\x00\x01binary")
    scan_root(conn, root)
    stats = run_queue(conn, embedder)
    assert stats.skipped == 1
    assert conn.execute("SELECT status FROM files").fetchone()[0] == "skipped"


def test_reindex_replaces_chunks_and_vectors(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    model_id = db.find_model(conn, embedder.model_info())
    n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    n_vecs = conn.execute(f"SELECT COUNT(*) FROM {db.vec_table(model_id)}").fetchone()[0]
    # re-run the same jobs: identical result, no duplicates
    for (fid,) in conn.execute("SELECT id FROM files").fetchall():
        jobs.enqueue(conn, fid, "index")
    run_queue(conn, embedder)
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == n_chunks
    assert conn.execute(f"SELECT COUNT(*) FROM {db.vec_table(model_id)}").fetchone()[0] == n_vecs == n_chunks
    assert conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == n_chunks


def test_delete_file_removes_fts_and_vectors_of_all_models(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    other = FakeEmbedder(dim=32)  # a second model: separate vector table
    with db.transaction(conn):
        other_id = db.ensure_model(conn, other.model_info())
        chunk_id = conn.execute("SELECT id FROM chunks LIMIT 1").fetchone()[0]
        conn.execute(
            f"INSERT INTO {db.vec_table(other_id)}(chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, np.ones(32, dtype=np.float32).tobytes()),
        )
    with db.transaction(conn):
        db.delete_files(conn, [r[0] for r in conn.execute("SELECT id FROM files").fetchall()])
    model_id = db.find_model(conn, embedder.model_info())
    for table in ("chunks", "chunks_fts", db.vec_table(model_id), db.vec_table(other_id)):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table


def test_models_get_separate_vector_tables(conn):
    a = db.ensure_model(conn, FakeEmbedder(dim=64).model_info())
    b = db.ensure_model(conn, FakeEmbedder(dim=32).model_info())
    assert a != b and db.ensure_model(conn, FakeEmbedder(dim=64).model_info()) == a


def test_indexed_files_are_never_modified(conn, project, embedder):
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in project.rglob("*") if p.is_file()}
    scan_root(conn, project)
    run_queue(conn, embedder)
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in project.rglob("*") if p.is_file()}
    assert before == after


# Letters FTS5 (`remove_diacritics 2`) does not fold by itself, each with the plain spelling a user types.
UNFOLDED = {"Łódź": "lodz", "Søren": "soren", "Đorđe": "djordje", "Ħamrun": "hamrun", "Straße": "strasse",
            "Ærø": "aero", "Œuvre": "oeuvre", "Þór": "thor", "Gaðar": "gadar", "Ŋaŋ": "nan", "Kırık": "kirik",
            "ﬁle": "file"}  # fmt: skip


def test_fts5_alone_leaves_these_letters_unfolded():
    import sqlite3

    raw = sqlite3.connect(":memory:")
    raw.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize = 'unicode61 remove_diacritics 2')")
    raw.executemany("INSERT INTO t VALUES (?)", [(word,) for word in UNFOLDED])
    folded_by_fts = [q for q in UNFOLDED.values() if raw.execute("SELECT 1 FROM t WHERE t MATCH ?", (q,)).fetchone()]
    assert folded_by_fts == [], "FTS5 learnt to fold some of these: the table in textnorm may shrink"


def test_keyword_search_folds_letters_without_a_decomposition(conn, tmp_path, embedder):
    from lantern.search.basic import keyword_search
    from lantern.textnorm import fold

    assert fold("Đorđe") == "Dorde" and fold("ﬁle") == "file" and fold("plain ascii") == "plain ascii"
    root = tmp_path / "names"
    for i, word in enumerate(UNFOLDED):
        write(root, f"n{i}.txt", f"Notatka o {word} z podróży.\n")
    scan_root(conn, root)
    run_queue(conn, embedder)
    for word, typed in UNFOLDED.items():
        typed = {"djordje": "dorde"}.get(typed, typed)  # đ is d; "dj" is a transliteration, not a fold
        for query in (typed, word):
            hits = keyword_search(conn, query, limit=5)
            assert hits and word in Path(hits[0].path).read_text(), (word, query)


def test_migration_refolds_existing_fts_rows(lantern_home):
    path = lantern_home / "old.db"
    conn = db.connect(path)
    conn.execute("INSERT INTO chunks_fts(rowid, text, path, symbol) VALUES (1, 'Søren w Lodzi', 'a/Straße.md', '')")
    conn.execute("PRAGMA user_version = 4")
    conn.close()
    conn = db.connect(path)
    assert tuple(conn.execute("SELECT text, path FROM chunks_fts").fetchone()) == ("Soren w Lodzi", "a/Strasse.md")
    assert conn.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'soren'").fetchone()[0] == 1

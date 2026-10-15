import json

from conftest import write

from lantern.embedding.fake import FakeEmbedder
from lantern.indexer import PIPELINE_VERSIONS, run_queue
from lantern.scanner.diff import scan_root
from lantern.search.basic import Hit
from lantern.search.hybrid import fuse, last_event, mark_opened, search
from lantern.store import db


def hit(path, score=1.0, chunk_id=1):
    return Hit(chunk_id, path, 1, 2, None, score, "preview")


def test_rrf_prefers_files_found_by_both_lists():
    vector = [hit("a"), hit("b"), hit("c")]
    keyword = [hit("c"), hit("d")]
    results = fuse(vector, keyword, limit=10)
    assert results[0].path == "c"  # rank 3 + rank 1 beats rank 1 alone
    c = results[0]
    assert (c.vector_rank, c.keyword_rank) == (3, 1)
    assert {r.path for r in results} == {"a", "b", "c", "d"}


def test_keyword_only_exact_identifier_survives_fusion(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    resp = search(conn, embedder, "backoff_wrapper", limit=3)
    top = resp.results[0]
    assert top.path.endswith("net.py") and top.keyword_rank == 1


def test_search_is_logged_with_features_and_open_is_recorded(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    resp = search(conn, embedder, "drzewa decyzyjne", limit=5)
    event = last_event(conn)
    assert event["id"] == resp.event_id and event["query"] == "drzewa decyzyjne"
    logged = json.loads(event["results_json"])
    assert logged[0]["rank"] == 1 and "vector_rank" in logged[0] and "keyword_score" in logged[0]
    assert "preview" not in logged[0]  # no file content in the history
    mark_opened(conn, event["id"], logged[0]["chunk_id"])
    assert conn.execute("SELECT opened_chunk_id FROM search_events").fetchone()[0] == logged[0]["chunk_id"]


def test_search_without_logging(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    search(conn, embedder, "cokolwiek", log=False)
    assert conn.execute("SELECT COUNT(*) FROM search_events").fetchone()[0] == 0


def test_migration_from_schema_v1(lantern_home):
    path = lantern_home / "old.db"
    conn = db.connect(path)
    conn.execute("ALTER TABLE files DROP COLUMN pipeline_version")
    conn.execute("ALTER TABLE files DROP COLUMN model_id")
    conn.execute("PRAGMA user_version = 1")
    conn.close()
    conn = db.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(files)")}
    assert {"pipeline_version", "model_id"} <= cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_pipeline_version_change_reindexes(conn, project, embedder, monkeypatch):
    scan_root(conn, project)
    run_queue(conn, embedder)
    before = embedder.texts_embedded
    assert run_queue(conn, embedder).indexed == 0  # nothing stale
    monkeypatch.setitem(PIPELINE_VERSIONS, "text", 99)  # only text files: code files are not touched
    stats = run_queue(conn, embedder)
    assert stats.stale == 2 and stats.indexed == 2 and embedder.texts_embedded > before


def test_model_change_reindexes_into_new_table(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    old_id = db.find_model(conn, embedder.model_info())
    other = FakeEmbedder(dim=32)
    stats = run_queue(conn, other)
    new_id = db.find_model(conn, other.model_info())
    assert stats.stale == 3 and new_id != old_id
    n_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert conn.execute(f"SELECT COUNT(*) FROM {db.vec_table(new_id)}").fetchone()[0] == n_chunks
    assert conn.execute(f"SELECT COUNT(*) FROM {db.vec_table(old_id)}").fetchone()[0] == 0  # never mixed


def test_remove_root_and_compact_erase_data_from_the_file(lantern_home, tmp_path, embedder):
    marker = "zxqmarkerunikalny"
    root = tmp_path / "secret-project"
    write(root, "plan.md", f"# Plan\n\nTajny projekt {marker} i jego szczegóły.\n" * 50)
    for i in range(30):
        write(root, f"doc{i}.md", f"dokument {i} " * 200)
    path = lantern_home / "index.db"
    conn = db.connect(path)
    scan_root(conn, root)
    run_queue(conn, embedder)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert marker.encode() in path.read_bytes()

    root_id = conn.execute("SELECT id FROM roots").fetchone()[0]
    with db.transaction(conn):
        db.remove_root(conn, root_id)
    db.compact(conn)
    conn.close()
    raw = path.read_bytes() + (
        path.with_name("index.db-wal").read_bytes() if path.with_name("index.db-wal").exists() else b""
    )
    assert marker.encode() not in raw

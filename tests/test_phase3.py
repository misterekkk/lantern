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
    conn.execute("DROP TABLE symbols")
    conn.execute("ALTER TABLE chunks DROP COLUMN page")
    conn.execute("PRAGMA user_version = 1")
    conn.close()
    conn = db.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(files)")}
    assert {"pipeline_version", "model_id"} <= cols
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'symbols'").fetchone()  # v2 -> v3
    assert "page" in {r[1] for r in conn.execute("PRAGMA table_info(chunks)")}  # v3 -> v4
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_pipeline_version_change_reindexes(conn, project, embedder, monkeypatch):
    scan_root(conn, project)
    run_queue(conn, embedder)
    before = embedder.texts_embedded
    assert run_queue(conn, embedder).indexed == 0  # nothing stale
    monkeypatch.setitem(PIPELINE_VERSIONS, "text", 99)  # only text files: code files are not touched
    stats = run_queue(conn, embedder)
    assert stats.stale == 2 and stats.indexed == 2 and embedder.texts_embedded > before


PAGE = """<!DOCTYPE html>
<html><head><title>Zamówienia &amp; faktury</title>
<style>.price { color: red }</style>
<script>var hidden = "<p>skryptowezdanie</p>";</script></head>
<body><h1>Zamówienie</h1><p>Kwota: <b>187</b>,15 zł za prąd</p>
<table><tr><td>Licznik</td><td>320 kWh</td></tr></table>
</body></html>
"""


def test_html_is_indexed_as_the_text_a_reader_sees_on_its_source_lines(conn, tmp_path, embedder):
    from lantern.extractors.text import document_text

    page = write(tmp_path / "docs", "zamowienie.html", PAGE)
    text = document_text(page)
    assert text.count("\n") == PAGE.count("\n")  # same lines: results open there, previews show the same text
    lines = text.split("\n")
    assert lines[1] == "Zamówienia & faktury" and lines[4] == "Zamówienie Kwota: 187,15 zł za prąd"
    assert lines[5] == "Licznik 320 kWh" and lines[2] == lines[3] == ""  # style and script: nothing
    scan_root(conn, tmp_path / "docs")
    run_queue(conn, embedder)
    chunks = conn.execute("SELECT text, start_line, end_line FROM chunks").fetchall()
    joined = " ".join(c["text"] for c in chunks)
    assert "<" not in joined and "skryptowezdanie" not in joined and "color" not in joined
    assert any("187,15" in c["text"] and c["start_line"] <= 5 <= c["end_line"] for c in chunks)


def test_an_html_version_change_reindexes_only_html_pages(conn, tmp_path, embedder, monkeypatch):
    from lantern import indexer

    write(tmp_path / "docs", "zamowienie.html", PAGE)
    write(tmp_path / "docs", "notatki.txt", "Kwota za prąd do zapłacenia w piątek.")
    scan_root(conn, tmp_path / "docs")
    run_queue(conn, embedder)
    assert run_queue(conn, embedder).stale == 0
    monkeypatch.setattr(indexer, "HTML_PIPELINE_VERSION", indexer.HTML_PIPELINE_VERSION + 1)
    stats = run_queue(conn, embedder)
    assert stats.stale == 1 and stats.indexed == 1  # the page, not the .txt
    # an index from before HTML had its own version: its pages carry the text version and are indexed once more
    conn.execute("UPDATE files SET pipeline_version = ? WHERE path LIKE '%.html'", (PIPELINE_VERSIONS["text"],))
    assert run_queue(conn, embedder).stale == 1
    assert run_queue(conn, embedder).stale == 0


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


def test_term_coverage():
    from lantern.textnorm import term_coverage

    assert term_coverage("urodziny babci w ogrodzie cioci", "Sadzenie pomidorów w ogrodzie") == 0.25
    assert term_coverage("krowa", "jakaś krowa stała na drodze") == 1.0
    assert term_coverage("kradzież roweru", "ktoś ukradł jej rower z piwnicy") == 0.5  # "rowe*" matches, "kradz*" not
    assert term_coverage("dobór głębokości", "dobor glebokosci drzewa") == 1.0  # diacritics-insensitive


def test_incidental_keyword_match_does_not_beat_top_vector_hit():
    vector = [hit("en_party"), hit("pl_long"), hit("other")]
    weak = Hit(1, "pl_long", None, None, None, 5.0, "", coverage=0.25)  # shares one word with the query
    assert fuse(vector, [weak], limit=3)[0].path == "en_party"
    strong = Hit(1, "pl_long", None, None, None, 5.0, "", coverage=1.0)
    assert fuse(vector, [strong], limit=3)[0].path == "pl_long"  # a real keyword match still counts

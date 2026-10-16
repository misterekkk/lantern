"""Panel search helpers: filter categories (pills), inline completion, search-as-you-type history, frames."""

from conftest import write

from lantern.indexer import run_queue
from lantern.scanner.diff import scan_root
from lantern.search.basic import Hit
from lantern.search.hybrid import last_event, mark_opened, photo_results, search
from lantern.search.suggest import completion


def project(tmp_path):
    root = tmp_path / "p"
    code = "def backoff_wrapper(fn, retries=3):\n    return fn()\n\n\ndef backoff_delay(n):\n    return 2 ** n\n"
    write(root, "src/net.py", code)
    write(root, "docs/retry-notes.md", "# Retry\n\nBackoff with retries for flaky network calls.\n")
    return root


def test_categories_and_filter(conn, tmp_path, embedder):
    scan_root(conn, project(tmp_path))
    run_queue(conn, embedder)
    resp = search(conn, embedder, "backoff retries", log=False)
    assert resp.categories == ["code", "docs"] and resp.category is None
    only_docs = search(conn, embedder, "backoff retries", log=False, category="docs")
    assert only_docs.category == "docs" and only_docs.definitions == []
    assert {r.kind for r in only_docs.results} == {"text"}
    assert search(conn, embedder, "backoff retries", log=False, category="nonsense").category is None


def test_completion_from_opened_history_names_and_files(conn, tmp_path, embedder):
    scan_root(conn, project(tmp_path))
    run_queue(conn, embedder)
    assert completion(conn, "backoff_w") == "backoff_wrapper"  # a definition
    assert completion(conn, "BACKOFF_D") == "BACKOFF_Delay"  # keeps what was typed, completes the rest
    assert completion(conn, "retry-n") == "retry-notes.md"  # a file name
    assert completion(conn, "x") is None

    resp = search(conn, embedder, "flaky network calls", log=True)
    assert completion(conn, "flaky n") is None  # searched while typing but nothing opened: not a suggestion
    mark_opened(conn, resp.event_id, resp.results[0].chunk_id)
    assert completion(conn, "flaky n") == "flaky network calls"


def test_typing_replaces_the_shorter_query_in_history(conn, tmp_path, embedder):
    scan_root(conn, project(tmp_path))
    run_queue(conn, embedder)
    for partial in ("ba", "back", "backoff"):
        search(conn, embedder, partial, log=True)
    assert conn.execute("SELECT COUNT(*) FROM search_events").fetchone()[0] == 1
    assert last_event(conn)["query"] == "backoff"
    mark_opened(conn, last_event(conn)["id"], 1)
    search(conn, embedder, "backoff wrapper", log=True)  # after an open: a new entry
    assert conn.execute("SELECT COUNT(*) FROM search_events").fetchone()[0] == 2


def test_video_frames_keep_their_moment_in_the_photos_section():
    frame = Hit(chunk_id=7, path="/v/trip.mov", start_line=None, end_line=None, symbol=None, score=2.0, preview="",
                kind="frame", start_ms=125_000, end_ms=150_000)  # fmt: skip
    photo = Hit(chunk_id=8, path="/p/dog.jpg", start_line=None, end_line=None, symbol=None, score=1.5, preview="",
                kind="image")  # fmt: skip
    results = photo_results([frame, photo], [])
    assert (results[0].kind, results[0].start_ms) == ("frame", 125_000) and results[1].kind == "image"

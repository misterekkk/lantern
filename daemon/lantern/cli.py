"""Command-line interface (single process, no daemon yet)."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from lantern import __version__, config


def _transcriber(enabled: bool):
    if not enabled:
        return None
    if os.environ.get("LANTERN_EMBEDDER") == "fake":
        from lantern.transcription.whisper import FakeTranscriber

        return FakeTranscriber(os.environ.get("LANTERN_FAKE_TRANSCRIPT", "fake transcript"))
    from lantern.transcription.whisper import WhisperTranscriber

    return WhisperTranscriber()


def _embedder():
    # LANTERN_EMBEDDER=fake is for tests and demos without the model; never the default.
    if os.environ.get("LANTERN_EMBEDDER") == "fake":
        from lantern.embedding.fake import FakeEmbedder

        return FakeEmbedder()
    from lantern.embedding.mlx_backend import MLXEmbedder

    return MLXEmbedder()


def _connect():
    from lantern.store import db

    return db.connect(config.db_path())


def _clock(ms: int) -> str:
    s = ms // 1000
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _location(r) -> str:
    if r.start_ms is not None:
        return f"{r.path} @ {_clock(r.start_ms)}–{_clock(r.end_ms)}"
    lines = f":{r.start_line}-{r.end_line}" if r.start_line else ""
    sym = f"  [{r.symbol}]" if r.symbol else ""
    return f"{r.path}{lines}{sym}"


def _sources(r) -> str:
    parts = []
    if r.vector_rank:
        parts.append(f"meaning #{r.vector_rank}")
    if r.keyword_rank:
        parts.append(f"keywords #{r.keyword_rank}")
    return " · ".join(parts)


def cmd_index(args: argparse.Namespace) -> int:
    from lantern.indexer import run_queue
    from lantern.scanner.diff import MassDeletionRefused, RootUnavailable, scan_root
    from lantern.store import db

    conn = _connect()
    root = Path(args.path).expanduser().resolve()
    t0 = time.perf_counter()
    try:
        scan = scan_root(conn, root, force=args.force)
    except RootUnavailable as exc:
        print(f"error: {exc}. Nothing was removed from the index.", file=sys.stderr)
        return 1
    except MassDeletionRefused as exc:
        print(f"error: {exc}. Check that the folder is complete, then re-run with --force.", file=sys.stderr)
        return 1
    print(
        f"scan {root}: {len(scan.new)} new, {len(scan.changed)} changed, {len(scan.moved)} moved, "
        f"{len(scan.deleted)} removed, {scan.unchanged + len(scan.touched)} unchanged "
        f"({time.perf_counter() - t0:.1f}s)"
    )

    def progress(stage: str, done: int, total: int, path: str) -> None:
        if sys.stderr.isatty():
            label = "indexing" if stage == "index" else "transcribing"
            name = Path(path).name[:40]
            print(f"\r  {label} {done}/{total} {name:<40}", end="", file=sys.stderr, flush=True)

    t1 = time.perf_counter()
    stats = run_queue(conn, _embedder(), progress, _transcriber(not args.no_transcribe))
    if sys.stderr.isatty():
        print("\r" + " " * 70 + "\r", end="", file=sys.stderr)
    stale = f", {stats.stale} re-indexed after a pipeline/model change" if stats.stale else ""
    print(
        f"indexed {stats.indexed} files ({stats.chunks} chunks) in {time.perf_counter() - t1:.1f}s, "
        f"skipped {stats.skipped}, failed {stats.failed}{stale}"
    )
    if stats.media or stats.transcribed:
        print(f"audio/video: {stats.media} files, {stats.audio_windows} audio windows, {stats.transcribed} transcribed")
    for path, msg in stats.errors[:10]:
        print(f"  failed: {path}: {msg}", file=sys.stderr)
    if scan.deleted and db.maybe_compact(conn):
        print("compacted the index (removed data physically erased)")
    return 0 if stats.failed == 0 else 3


def cmd_search(args: argparse.Namespace) -> int:
    from lantern.search.hybrid import search

    conn = _connect()
    embedder = _embedder()
    if hasattr(embedder, "load"):
        embedder.load()  # keep model loading out of the query timing
    resp = search(conn, embedder, args.query, args.limit, log=not args.no_log)
    print(f"{len(resp.results)} results ({resp.vector_ms + resp.keyword_ms:.0f} ms)")
    for i, r in enumerate(resp.results, 1):
        print(f"  {i:2d}. {_location(r)}   ({_sources(r)})")
        print(f"      {r.preview or '(audio match)'}")
    if resp.results and resp.event_id:
        print("open a result with: lantern open <number>")
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    from lantern.search.hybrid import last_event, mark_opened

    conn = _connect()
    event = last_event(conn)
    results = json.loads(event["results_json"]) if event else []
    if not 1 <= args.number <= len(results):
        print(f"error: no result #{args.number} in the last search", file=sys.stderr)
        return 1
    hit = results[args.number - 1]
    mark_opened(conn, event["id"], hit["chunk_id"])
    if args.print:
        where = f" @ {_clock(hit['start_ms'])}" if hit.get("start_ms") is not None else ""
        print(hit["path"] + (f":{hit['start_line']}" if hit["start_line"] else "") + where)
        return 0
    if not Path(hit["path"]).exists():
        print(f"error: file no longer exists: {hit['path']}", file=sys.stderr)
        return 1
    # Paths are absolute, so they can never be mistaken for an option of `open`.
    subprocess.run(["open", "-R", hit["path"]] if args.reveal else ["open", hit["path"]], check=False)
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    from lantern.store import db

    conn = _connect()
    root = str(Path(args.path).expanduser().resolve())
    row = conn.execute("SELECT id FROM roots WHERE path = ?", (root,)).fetchone()
    if row is None:
        print(f"error: {root} is not an indexed folder", file=sys.stderr)
        return 1
    with db.transaction(conn):
        n = db.remove_root(conn, row["id"])
    db.compact(conn)
    print(f"removed {root} from the index ({n} files) and compacted the database")
    return 0


def cmd_compact(args: argparse.Namespace) -> int:
    from lantern.store import db

    conn = _connect()
    before = config.db_path().stat().st_size
    db.compact(conn)
    print(f"compacted: {before / 2**20:.1f} MB -> {config.db_path().stat().st_size / 2**20:.1f} MB")
    return 0


def cmd_clear_history(args: argparse.Namespace) -> int:
    from lantern.search.hybrid import clear_history
    from lantern.store import db

    conn = _connect()
    n = clear_history(conn)
    db.compact(conn)
    print(f"deleted {n} search history entries")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from lantern.queue import jobs
    from lantern.store import db

    conn = _connect()
    ratio, _ = db.free_ratio(conn)
    print(f"index: {config.db_path()} ({config.db_path().stat().st_size / 2**20:.1f} MB, {ratio:.0%} free pages)")
    for r in conn.execute("SELECT path, last_scan FROM roots ORDER BY path"):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_scan"])) if r["last_scan"] else "never"
        print(f"root: {r['path']} (last scan {when})")
    files = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM files GROUP BY status")}
    chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    print(f"files: {sum(files.values())} " + " ".join(f"{k}={v}" for k, v in sorted(files.items())))
    print(f"chunks: {chunks}")
    print("jobs: " + (" ".join(f"{k}={v}" for k, v in sorted(jobs.counts(conn).items())) or "none"))
    for r in conn.execute("SELECT name, revision, backend, dtype, dim FROM embedding_models"):
        print(f"model: {r['name']}@{r['revision'][:8]} ({r['backend']}, {r['dtype']}, {r['dim']}d)")
    events, opened = conn.execute("SELECT COUNT(*), COUNT(opened_chunk_id) FROM search_events").fetchone()
    print(f"search history: {events} searches, {opened} with an opened result")
    for r in conn.execute("SELECT path, error FROM files WHERE status = 'error' LIMIT 5"):
        print(f"  error: {r['path']}: {r['error']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lantern", description="Local, offline semantic file search.")
    parser.add_argument("--version", action="version", version=f"lantern {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index", help="index a folder or update it (read-only access to files)")
    p.add_argument("path", help="folder to index")
    p.add_argument("--force", action="store_true", help="allow removing more than half of a folder's entries")
    p.add_argument("--no-transcribe", action="store_true", help="index audio/video without Whisper transcription")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("search", help="search by meaning and keywords (one fused ranking)")
    p.add_argument("query", help="search query")
    p.add_argument("-n", "--limit", type=int, default=10, help="number of results")
    p.add_argument("--no-log", action="store_true", help="do not record this search in the local history")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("open", help="open result N of the last search (and record the choice)")
    p.add_argument("number", type=int)
    p.add_argument("--reveal", action="store_true", help="show in Finder instead of opening")
    p.add_argument("--print", action="store_true", help="only print the path")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("remove", help="remove a folder and all its data from the index")
    p.add_argument("path")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("compact", help="physically erase deleted data from the index file (VACUUM)")
    p.set_defaults(func=cmd_compact)

    p = sub.add_parser("clear-history", help="delete the local search history")
    p.set_defaults(func=cmd_clear_history)

    p = sub.add_parser("status", help="show index and queue status")
    p.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

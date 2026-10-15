"""Command-line interface: index, search, status (single process, no daemon yet)."""

import argparse
import os
import sys
import time
from pathlib import Path

from lantern import __version__, config


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


def _location(hit) -> str:
    lines = f":{hit.start_line}-{hit.end_line}" if hit.start_line else ""
    symbol = f"  [{hit.symbol}]" if hit.symbol else ""
    return f"{hit.path}{lines}{symbol}"


def cmd_index(args: argparse.Namespace) -> int:
    from lantern.indexer import run_queue
    from lantern.scanner.diff import MassDeletionRefused, RootUnavailable, scan_root

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

    def progress(done: int, total: int, path: str) -> None:
        if sys.stderr.isatty():
            name = Path(path).name[:40]
            print(f"\r  indexing {done}/{total} {name:<40}", end="", file=sys.stderr, flush=True)

    t1 = time.perf_counter()
    stats = run_queue(conn, _embedder(), progress)
    if sys.stderr.isatty():
        print("\r" + " " * 70 + "\r", end="", file=sys.stderr)
    dt = time.perf_counter() - t1
    print(
        f"indexed {stats.indexed} files ({stats.chunks} chunks) in {dt:.1f}s, "
        f"skipped {stats.skipped}, failed {stats.failed}"
    )
    for path, msg in stats.errors[:10]:
        print(f"  failed: {path}: {msg}", file=sys.stderr)
    return 0 if stats.failed == 0 else 3


def cmd_search(args: argparse.Namespace) -> int:
    from lantern.search.basic import keyword_search, vector_search

    conn = _connect()
    embedder = _embedder()
    if hasattr(embedder, "load"):
        embedder.load()  # keep model loading out of the query timing
    t0 = time.perf_counter()
    semantic = vector_search(conn, embedder, args.query, args.limit)
    t1 = time.perf_counter()
    keyword = keyword_search(conn, args.query, args.limit)
    t2 = time.perf_counter()

    print(f"By meaning ({(t1 - t0) * 1000:.0f} ms):")
    for i, h in enumerate(semantic, 1):
        print(f"  {i:2d}. {h.score:.3f}  {_location(h)}\n      {h.preview}")
    if not semantic:
        print("  (no results)")
    print(f"By keywords ({(t2 - t1) * 1000:.0f} ms):")
    for i, h in enumerate(keyword, 1):
        print(f"  {i:2d}. {_location(h)}\n      {h.preview}")
    if not keyword:
        print("  (no results)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from lantern.queue import jobs

    conn = _connect()
    print(f"index: {config.db_path()} ({config.db_path().stat().st_size / 2**20:.1f} MB)")
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
    for r in conn.execute("SELECT path, error FROM files WHERE status = 'error' LIMIT 5"):
        print(f"  error: {r['path']}: {r['error']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lantern", description="Local, offline semantic file search.")
    parser.add_argument("--version", action="version", version=f"lantern {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="index a folder (read-only access to files)")
    p_index.add_argument("path", help="folder to index")
    p_index.add_argument("--force", action="store_true", help="allow removing more than half of a folder's entries")
    p_index.set_defaults(func=cmd_index)

    p_search = sub.add_parser("search", help="search the index by meaning and keywords")
    p_search.add_argument("query", help="search query")
    p_search.add_argument("-n", "--limit", type=int, default=10, help="number of results per list")
    p_search.set_defaults(func=cmd_search)

    p_status = sub.add_parser("status", help="show index and queue status")
    p_status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

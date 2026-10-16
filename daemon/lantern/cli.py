"""Command-line interface.

When the daemon is running, the CLI is a client of its API (searches use the warm model, writes go through the
daemon so that only one process indexes). Without the daemon, commands work directly on the index.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from lantern import __version__, config
from lantern.api import protocol


def _connect():
    from lantern.store import db

    return db.connect(config.db_path())


def _settings():
    from lantern.service import settings as settings_mod

    return settings_mod.load()


def _walk_options() -> dict:
    from lantern.service.settings import walk_options

    return walk_options(_settings())


def _daemon() -> bool:
    return protocol.running()


def _need_daemon() -> None:
    if not _daemon():
        print("error: the daemon is not running (start it with `lantern daemon run` or `lantern daemon install`)",
              file=sys.stderr)  # fmt: skip
        raise SystemExit(1)


def _clock(ms: int) -> str:
    s = ms // 1000
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _location(r) -> str:
    if getattr(r, "page", None):
        return f"{r.path}  (page {r.page})"
    if r.start_ms is not None:
        return f"{r.path} @ {_clock(r.start_ms)}–{_clock(r.end_ms)}"
    lines = f":{r.start_line}-{r.end_line}" if r.start_line else ""
    sym = f"  [{r.symbol}]" if r.symbol else ""
    return f"{r.path}{lines}{sym}"


def _sources(r) -> str:
    parts = []
    if r.vector_rank:
        parts.append(f"meaning #{r.vector_rank}")
    if getattr(r, "image_rank", None):
        parts.append(f"image #{r.image_rank}")
    if r.keyword_rank:
        parts.append(f"keywords #{r.keyword_rank}")
    return " · ".join(parts)


def _print_scan(s: dict) -> None:
    if "error" in s:
        print(f"scan {s['root']}: {s['error']}", file=sys.stderr)
        return
    print(
        f"scan {s['root']}: {s['new']} new, {s['changed']} changed, {s['moved']} moved, "
        f"{s['removed']} removed, {s['unchanged']} unchanged"
    )


# ---------- index / search ----------


# Rough rates measured on an M4 (DECISIONS.md, phases 2-4): text/code ~17 chunks/s of ~1000 characters;
# audio/video ~10x real time including transcription (~1 MB per minute of compressed audio as a size proxy).
_TEXT_BYTES_PER_S = 17_000
_MEDIA_BYTES_PER_S = 1024 * 1024 * 10 / 60


def cmd_index_dry_run(root: Path) -> int:
    from collections import Counter

    from lantern.config import MAX_TEXT_FILE_BYTES
    from lantern.scanner.walk import WalkReport, iter_files

    report = WalkReport()
    counts, sizes = Counter(), Counter()
    t0 = time.perf_counter()
    for entry in iter_files(root, report, **_walk_options()):
        counts[entry.kind] += 1
        if entry.kind in ("text", "code"):
            sizes[entry.kind] += min(entry.size, MAX_TEXT_FILE_BYTES)
        else:
            sizes[entry.kind] += entry.size
    print(f"dry run for {root} ({time.perf_counter() - t0:.1f}s, nothing read or written):")
    for kind in ("code", "text", "audio", "video"):
        if counts[kind]:
            print(f"  {kind:6s} {counts[kind]:7d} files  {sizes[kind] / 2**20:9.1f} MB")
    text_s = (sizes["code"] + sizes["text"]) / _TEXT_BYTES_PER_S
    media_s = (sizes["audio"] + sizes["video"]) / _MEDIA_BYTES_PER_S
    print(
        f"  skipped: {report.cloud_only} iCloud items not on this disk, {len(report.unreadable)} folders without access"
    )
    for p in report.unreadable[:10]:
        print(f"    no access: {p}")
    if report.images:
        print(f"  photos (not indexed, switched off): {report.images}")
    print(f"  rough first-index time: text/code ~{text_s / 3600:.1f} h, audio/video ~{media_s / 3600:.1f} h "
          "(later runs only process changes)")  # fmt: skip
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    if args.dry_run:
        return cmd_index_dry_run(Path(args.path).expanduser().resolve())
    if _daemon():
        try:
            summary = protocol.call("index", {"path": args.path, "force": args.force}, timeout=600)
        except protocol.DaemonError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        _print_scan(summary)
        if summary.get("error") == "mass_deletion_refused":
            print("check that the folder is complete, then re-run with --force", file=sys.stderr)
            return 1
        print("indexing in the background (daemon); follow it with `lantern status`")
        return 0

    from lantern.indexer import run_queue
    from lantern.scanner.diff import MassDeletionRefused, RootUnavailable, scan_root
    from lantern.service.worker import make_embedder, make_image_embedder, make_transcriber
    from lantern.store import db

    conn = _connect()
    root = Path(args.path).expanduser().resolve()
    t0 = time.perf_counter()
    try:
        settings = _settings()
        scan = scan_root(conn, root, force=args.force, **_walk_options())
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
    stats = run_queue(
        conn, make_embedder(), progress, make_transcriber(not args.no_transcribe),
        image_embedder=make_image_embedder(settings), video_frames=settings.index_video_frames,
        image_text=settings.index_image_text,
    )  # fmt: skip
    if sys.stderr.isatty():
        print("\r" + " " * 70 + "\r", end="", file=sys.stderr)
    stale = f", {stats.stale} re-indexed after a pipeline/model change" if stats.stale else ""
    print(
        f"indexed {stats.indexed} files ({stats.chunks} chunks) in {time.perf_counter() - t1:.1f}s, "
        f"skipped {stats.skipped}, failed {stats.failed}{stale}"
    )
    if stats.media or stats.transcribed:
        print(f"audio/video: {stats.media} files, {stats.audio_windows} audio windows, {stats.transcribed} transcribed")
    if stats.images or stats.frames:
        print(f"photos: {stats.images} ({stats.image_texts} with text), video frames: {stats.frames}")
    for path, msg in stats.errors[:10]:
        print(f"  failed: {path}: {msg}", file=sys.stderr)
    if scan.deleted and db.maybe_compact(conn):
        print("compacted the index (removed data physically erased)")
    return 0 if stats.failed == 0 else 3


def cmd_search(args: argparse.Namespace) -> int:
    if _daemon():
        resp = protocol.call("search", {"query": args.query, "limit": args.limit, "log": not args.no_log,
                                        "category": args.only})  # fmt: skip
        sections = {name: [SimpleNamespace(**r) for r in resp.get(name, [])] for name in ("definitions", "photos")}
        sections["files"] = [SimpleNamespace(**r) for r in resp["results"]]
        media = ["photos", "files"] if resp.get("photos_first") else ["files", "photos"]
        ordered = [(name, sections[name]) for name in ["definitions", *media] if sections[name]]
        took = sum(resp.get(k, 0) for k in ("vector_ms", "keyword_ms", "image_ms", "symbol_ms"))
        logged = resp["event_id"]
    else:
        from lantern.search.hybrid import search
        from lantern.service.worker import make_embedder, make_query_embedder
        from lantern.syslang import system_language

        embedder = make_embedder()
        if hasattr(embedder, "load"):
            embedder.load()  # keep model loading out of the query timing
        r = search(_connect(), embedder, args.query, args.limit, log=not args.no_log, lang=system_language(),
                   query_embedder=make_query_embedder(), category=args.only)  # fmt: skip
        ordered, logged = r.sections(), r.event_id
        took = r.vector_ms + r.keyword_ms + r.image_ms + r.symbol_ms
    print(f"{sum(len(items) for _, items in ordered)} results ({took:.0f} ms)")
    n = 0
    for name, items in ordered:
        print({"definitions": "Definitions:", "photos": "Photos:", "files": "Files:"}[name])
        for r in items:
            n += 1
            if name == "definitions":
                print(f"  {n:2d}. {r.path}:{r.line}   {r.signature}   ({r.kind}, {r.match})")
                continue
            print(f"  {n:2d}. {_location(r)}   ({_sources(r)})")
            if r.preview:
                print(f"      {r.preview}")
    if ordered and logged:
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


# ---------- maintenance ----------


def cmd_remove(args: argparse.Namespace) -> int:
    root = str(Path(args.path).expanduser().resolve())
    if _daemon():
        try:
            n = protocol.call("remove", {"path": root}, timeout=600)["files"]
        except protocol.DaemonError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    else:
        from lantern.store import db

        conn = _connect()
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
    if _daemon():
        r = protocol.call("compact", timeout=600)
        before, after = r["before"], r["after"]
    else:
        from lantern.store import db

        before = config.db_path().stat().st_size
        db.compact(_connect())
        after = config.db_path().stat().st_size
    print(f"compacted: {before / 2**20:.1f} MB -> {after / 2**20:.1f} MB")
    return 0


def cmd_clear_history(args: argparse.Namespace) -> int:
    if _daemon():
        n = protocol.call("clear_history", timeout=600)["deleted"]
    else:
        from lantern.search.hybrid import clear_history
        from lantern.store import db

        conn = _connect()
        n = clear_history(conn)
        db.compact(conn)
    print(f"deleted {n} search history entries")
    return 0


def _print_local_status() -> None:
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


def cmd_status(args: argparse.Namespace) -> int:
    if not _daemon():
        print("daemon: not running")
        _print_local_status()
        return 0
    s = protocol.call("status")
    paused = f", paused ({s['paused']})" if s["paused"] else ""
    print(f"daemon: running (pid {s['pid']}, {s['memory_mb']} MB), mode {s['mode']}{paused}")
    if power := s.get("power"):
        source = "battery" if power["on_battery"] else "AC power"
        low = ", Low Power Mode" if power["low_power"] else ""
        print(f"power: {source}{low}, thermal state {power['thermal']}")
    w = s["worker"]
    if w["running"]:
        stage = w.get("stage", "")
        where = f" {w.get('done', 0)}/{w.get('total', 0)} {Path(w.get('path', '')).name}" if "done" in w else ""
        print(f"indexing: {stage}{where}")
    queued = sum(s["queued"].values())
    if queued:
        detail = ", ".join(f"{k} {v}" for k, v in sorted(s["queued"].items()))
        note = " — waiting for approval (`lantern approve`)" if s["awaiting_approval"] else ""
        print(f"queued: {queued} ({detail}){note}")
    files = s["files"]
    print(f"files: {sum(files.values())} " + " ".join(f"{k}={v}" for k, v in sorted(files.items())))
    print(f"chunks: {s['chunks']}")
    for r in s["roots"]:
        state = "" if r["available"] else "  (not available: disconnected?)"
        print(f"root: {r['path']}{state}")
    if s["last_run"]:
        lr = s["last_run"]
        print(
            f"last run {lr['finished']}: {lr['indexed']} indexed, "
            f"{lr['transcribed']} transcribed, {lr['failed']} failed"
        )
    for w in s["warnings"]:
        print(f"  ! {w}")
    return 0


# ---------- daemon control ----------


def cmd_daemon(args: argparse.Namespace) -> int:
    from lantern.service import launchagent

    if args.action == "run":
        from lantern.service.core import Daemon

        config.enforce_offline()
        return Daemon().run()
    if args.action == "install":
        if args.print:
            sys.stdout.write(launchagent.render().decode())
            return 0
        path = launchagent.install()
        print(f"installed and started the LaunchAgent: {path}")
        return 0
    if args.action == "uninstall":
        print("LaunchAgent removed" if launchagent.uninstall() else "LaunchAgent was not installed")
        return 0
    if args.action == "stop":
        if not _daemon():
            print("daemon is not running")
            return 0
        protocol.call("shutdown")
        print("daemon is stopping (the current file is finished first)")
        return 0
    return 2


def cmd_pending(args: argparse.Namespace) -> int:
    _need_daemon()
    p = protocol.call("pending")
    queued = sum(p["queued"].values())
    if not queued:
        print("nothing waiting")
        return 0
    detail = ", ".join(f"{k} {v}" for k, v in sorted(p["queued"].items()))
    print(
        f"{queued} files waiting ({detail})" + (" — approve with `lantern approve`" if p["awaiting_approval"] else "")
    )
    return 0


def _simple(method: str, message: str):
    def run(args: argparse.Namespace) -> int:
        _need_daemon()
        protocol.call(method)
        print(message)
        return 0

    return run


def cmd_config(args: argparse.Namespace) -> int:
    from lantern.service import settings as settings_mod

    if args.key is None:
        current = protocol.call("config_get") if _daemon() else settings_mod.asdict(settings_mod.load())
        for k, v in current.items():
            print(f"{k} = {json.dumps(v)}")
        return 0
    if args.value is None:
        print("error: missing value", file=sys.stderr)
        return 1
    try:
        if _daemon():
            protocol.call("config_set", {"key": args.key, "value": args.value})
        else:
            s = settings_mod.load()
            settings_mod.update(s, args.key, args.value)
            settings_mod.save(s)
    except (KeyError, ValueError, protocol.DaemonError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{args.key} = {args.value}")
    return 0


def cmd_exclude(args: argparse.Namespace) -> int:
    from lantern.service import settings as settings_mod

    if args.action == "list" or args.pattern is None:
        current = protocol.call("config_get")["exclude"] if _daemon() else settings_mod.load().exclude
        print("\n".join(current) if current else "no extra exclusions (defaults and .gitignore still apply)")
        return 0
    try:
        if _daemon():
            protocol.call("exclude", {"action": args.action, "pattern": args.pattern})
        else:
            s = settings_mod.load()
            (settings_mod.add_exclude if args.action == "add" else settings_mod.remove_exclude)(s, args.pattern)
            settings_mod.save(s)
    except (KeyError, protocol.DaemonError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    verb = "excluded" if args.action == "add" else "no longer excluded"
    print(f"{verb}: {args.pattern} (applied at the next scan; already indexed matches are then removed)")
    return 0


def cmd_ignore(args: argparse.Namespace) -> int:
    from lantern.service import settings as settings_mod

    if args.action == "list" or not args.extensions:
        current = (
            protocol.call("config_get")["exclude_extensions"] if _daemon() else settings_mod.load().exclude_extensions
        )
        print(" ".join(current) if current else "no ignored extensions")
        return 0
    for raw in args.extensions:
        try:
            if _daemon():
                ext = protocol.call("extensions", {"action": args.action, "extension": raw})["changed"]
            else:
                s = settings_mod.load()
                ext = (settings_mod.add_extension if args.action == "add" else settings_mod.remove_extension)(s, raw)
                settings_mod.save(s)
        except (KeyError, ValueError, protocol.DaemonError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"{'ignored' if args.action == 'add' else 'indexed again'}: {ext}")
    print("applied at the next scan; already indexed files with these extensions are then removed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lantern", description="Local, offline semantic file search.")
    parser.add_argument("--version", action="version", version=f"lantern {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index", help="index a folder or update it (read-only access to files)")
    p.add_argument("path", help="folder to index")
    p.add_argument("--force", action="store_true", help="allow removing more than half of a folder's entries")
    p.add_argument("--no-transcribe", action="store_true", help="index audio/video without Whisper (no daemon)")
    p.add_argument("--dry-run", action="store_true", help="only count what would be indexed and estimate the time")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("search", help="search by meaning and keywords (one fused ranking)")
    p.add_argument("query", help="search query")
    p.add_argument("-n", "--limit", type=int, default=10, help="number of results")
    p.add_argument("--no-log", action="store_true", help="do not record this search in the local history")
    p.add_argument(
        "--only",
        choices=["code", "docs", "pdf", "photos", "media"],
        help="only one kind of result (the panel's filter pills)",
    )
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("open", help="open result N of the last search (and record the choice)")
    p.add_argument("number", type=int)
    p.add_argument("--reveal", action="store_true", help="show in Finder instead of opening")
    p.add_argument("--print", action="store_true", help="only print the path")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("status", help="show daemon, index and queue status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("remove", help="remove a folder and all its data from the index")
    p.add_argument("path")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("compact", help="physically erase deleted data from the index file (VACUUM)")
    p.set_defaults(func=cmd_compact)

    p = sub.add_parser("clear-history", help="delete the local search history")
    p.set_defaults(func=cmd_clear_history)

    p = sub.add_parser("daemon", help="run or manage the background daemon")
    p.add_argument("action", choices=["run", "install", "uninstall", "stop"])
    p.add_argument("--print", action="store_true", help="install: only print the LaunchAgent plist")
    p.set_defaults(func=cmd_daemon)

    p = sub.add_parser("pending", help="files found by the daemon that wait for approval")
    p.set_defaults(func=cmd_pending)
    for name, method, message, help_text in [
        ("approve", "approve", "approved: indexing starts in the background", "index the waiting files"),
        ("decline", "decline", "postponed: asked again after new changes", "do not index the waiting files now"),
        ("pause", "pause", "indexing paused", "pause background indexing"),
        ("resume", "resume", "indexing resumed", "resume background indexing"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=_simple(method, message))

    p = sub.add_parser("exclude", help="manage extra exclusions (paths or .gitignore-style patterns)")
    p.add_argument("action", choices=["add", "remove", "list"])
    p.add_argument("pattern", nargs="?")
    p.set_defaults(func=cmd_exclude)

    p = sub.add_parser("ignore", help="manage ignored file extensions (csv, .csv and *.csv all work)")
    p.add_argument("action", choices=["add", "remove", "list"])
    p.add_argument("extensions", nargs="*")
    p.set_defaults(func=cmd_ignore)

    p = sub.add_parser("config", help="show or change settings (index_mode ask|auto, pause_on_battery, ...)")
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.set_defaults(func=cmd_config)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

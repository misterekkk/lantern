"""Command-line interface.

When the daemon is running, the CLI is a client of its API (searches use the warm model, writes go through the
daemon so that only one process indexes). Without the daemon, commands work directly on the index.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from lantern import __version__, config, filemeta
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
    return " · ".join([*parts, *_made(r)])


def _made(r) -> list[str]:
    """When and where a photo or recording was made (the date of a document only when its name or contents say it)."""
    visual = getattr(r, "kind", "") in ("image", "frame") or getattr(r, "start_ms", None) is not None
    if not getattr(r, "date", None) or not (visual or getattr(r, "date_source", None) in ("exif", "quicktime", "name")):
        return []
    where = getattr(r, "place", None) or getattr(r, "country", None)
    return [r.date, *([where] if where else [])]


def _people_pending(conn) -> int:
    from lantern import people

    return people.pending(conn)


def _print_enriching(dates: int, people: int) -> None:
    """Background work after an update or new files: dates and places from headers, people in photos."""
    if dates:
        print(f"dates and places: {dates} files still to read (in the background, no re-indexing)")
    if people:
        print(f"people in photos: {people} photos still to count (in the background)")


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
        image_text=settings.index_image_text, people=settings.people and settings.index_photos,
    )  # fmt: skip
    while filemeta.refresh(conn, places=settings.places):  # dates and places (file headers only)
        pass
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


def _print_json(data) -> int:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    if _daemon():
        resp = protocol.call("search", {"query": args.query, "limit": args.limit, "log": not args.no_log,
                                        "category": args.only})  # fmt: skip
        if args.json:  # the daemon's reply as it is: sections, scores, dates and places (docs/CLI.md)
            return _print_json(resp)
        named = ("definitions", "photos", "apps", "settings", "shortcuts", "music")
        sections = {name: [SimpleNamespace(**r) for r in resp.get(name, [])] for name in named}
        sections["files"] = [SimpleNamespace(**r) for r in resp["results"]]
        media = ["photos", "files"] if resp.get("photos_first") else ["files", "photos"]
        names = ["definitions", *media]
        sections["answer"] = [SimpleNamespace(**resp["answer"])] if resp.get("answer") else []
        sections["definition"] = [SimpleNamespace(**resp["definition"])] if resp.get("definition") else []
        apps_first, settings_first = resp.get("apps_first"), resp.get("settings_first")
        word_first = resp.get("definition_first")
        first = ["answer", *(["apps"] if apps_first else []), "shortcuts", *(["definition"] if word_first else []),
                 *(["settings"] if settings_first else [])]  # fmt: skip
        last = [*([] if settings_first else ["settings"]), *([] if apps_first else ["apps"]),
                *([] if word_first else ["definition"])]  # fmt: skip
        names = resp["order"] if resp.get("order") is not None else first + names + last
        ordered = [(name, sections[name]) for name in names if sections[name]]
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
        if args.json:
            from dataclasses import asdict

            reply = asdict(r)
            reply["order"] = [name for name, _ in r.sections()]
            return _print_json(reply)
        ordered, logged = r.sections(), r.event_id
        took = r.vector_ms + r.keyword_ms + r.image_ms + r.symbol_ms
    print(f"{sum(len(items) for _, items in ordered)} results ({took:.0f} ms)")
    n = 0
    for name, items in ordered:
        print({"definitions": "Definitions:", "photos": "Photos:", "files": "Files:", "apps": "Applications:",
               "settings": "System Settings:", "shortcuts": "Shortcuts:", "answer": "Answer:",
               "definition": "Dictionary:", "music": "Music:"}[name])  # fmt: skip
        for r in items:
            n += 1
            if name == "apps":
                print(f"  {n:2d}. {r.name}   {r.path}   ({r.match})")
                continue
            if name == "answer":
                print(f"  {n:2d}. {r.title}   ({r.subtitle})")
                continue
            if name == "definition":
                print(f"  {n:2d}. {r.text}")
                continue
            if name == "shortcuts":
                print(f"  {n:2d}. {r.name}   (shortcut, {r.match})")
                continue
            if name == "music":
                if r.kind == "playlist":
                    print(f"  {n:2d}. {r.title}   (playlist, {r.count} songs)")
                else:
                    where = f", playlist {r.playlist_name}" if r.playlist_name else ""
                    print(f"  {n:2d}. {r.title} — {r.artist} · {r.album}   (song{where}, {r.match})")
                continue
            if name == "settings":
                where = "" if r.is_pane else f"   ({r.pane})"
                print(f"  {n:2d}. {r.title}{where}   {r.url}   ({r.match})")
                continue
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
    if hit["path"].startswith("music:"):
        print("error: songs and playlists play from the search panel (Music app)", file=sys.stderr)
        return 1
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
    _print_enriching(filemeta.pending(conn), _people_pending(conn))
    print("jobs: " + (" ".join(f"{k}={v}" for k, v in sorted(jobs.counts(conn).items())) or "none"))
    for r in conn.execute("SELECT name, revision, backend, dtype, dim FROM embedding_models"):
        print(f"model: {r['name']}@{r['revision'][:8]} ({r['backend']}, {r['dtype']}, {r['dim']}d)")
    events, opened = conn.execute("SELECT COUNT(*), COUNT(opened_chunk_id) FROM search_events").fetchone()
    print(f"search history: {events} searches, {opened} with an opened result")
    for r in conn.execute("SELECT path, error FROM files WHERE status = 'error' LIMIT 5"):
        print(f"  error: {r['path']}: {r['error']}")


def cmd_diagnostics(args: argparse.Namespace) -> int:
    if _daemon():
        print(protocol.call("diagnostics")["text"], end="")
        return 0
    from lantern.service import diagnostics

    conn = None
    try:
        conn = _connect() if config.db_path().exists() else None
    except Exception:  # noqa: BLE001 - a damaged or newer index: the report says it could not be opened
        conn = None
    print(diagnostics.report(None, conn), end="")
    return 0


def _local_status() -> dict:
    """What `status` says without a daemon, as data (`--json`)."""
    from lantern.queue import jobs
    from lantern.store import db

    conn = _connect()
    ratio, _ = db.free_ratio(conn)
    out = {
        "daemon": None,
        "index": {"path": str(config.db_path()), "size_mb": round(config.db_path().stat().st_size / 2**20, 1),
                  "free_pages": round(ratio, 3)},  # fmt: skip
        "roots": [{"path": r["path"], "last_scan": r["last_scan"]}
                  for r in conn.execute("SELECT path, last_scan FROM roots ORDER BY path")],  # fmt: skip
        "files": {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM files GROUP BY status")},
        "chunks": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        "jobs": jobs.counts(conn),
        "dates_pending": filemeta.pending(conn),
        "people_pending": _people_pending(conn),
    }
    conn.close()
    return out


def cmd_status(args: argparse.Namespace) -> int:
    if not _daemon():
        if args.json:
            return _print_json(_local_status())
        print("daemon: not running")
        _print_local_status()
        return 0
    s = protocol.call("status")
    if args.json:
        return _print_json(s)
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
    if "dates_pending" in s:  # a daemon from before 0.2 does not say
        _print_enriching(s["dates_pending"], s["people_pending"])
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
    if args.json:
        return _print_json(p)
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
        if args.json:
            return _print_json(current)
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
            if args.key in ("places", "people") and not getattr(s, args.key) and config.db_path().exists():
                from lantern import people  # switched off with no daemon: the data goes now, as the daemon would do

                conn = _connect()
                filemeta.forget_places(conn) if args.key == "places" else people.forget(conn)
                conn.close()
    except (KeyError, ValueError, protocol.DaemonError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{args.key} = {args.value}")
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    """The models, as in Settings → Performance and the welcome window: sizes, downloaded or not, what this Mac's
    memory recommends; `download` fetches them (once, from Hugging Face; everything else stays offline)."""
    from lantern import models as models_mod
    from lantern.service import machine

    mem = machine.read()
    prof = machine.profile(mem.total)
    rows = [
        {"key": m.key, "group": m.group, "label": m.label, "size_mb": m.approx_mb,
         "downloaded": models_mod.is_downloaded(m.repo, m.revision),
         "recommended": m.group == "required" or prof == "full",
         "needs_memory_gb": round(machine.NEEDS[m.key] / machine.GB, 1) if m.key in machine.NEEDS else None}
        for m in models_mod.catalogue()
    ]  # fmt: skip
    if args.action == "list":
        if args.json:
            return _print_json({"ram_gb": round(mem.total / machine.GB), "profile": prof, "models": rows})
        print(f"this Mac: {mem.total / machine.GB:.0f} GB of memory, profile {prof}")
        for r in rows:
            state = "downloaded" if r["downloaded"] else "not downloaded"
            hint = "" if r["recommended"] else "  (not recommended for this Mac's memory)"
            print(f"  {r['key']:15} {r['size_mb']:>5} MB  {state:15} {r['label']}{hint}")
        return 0
    wanted = args.names or [r["group"] for r in rows if r["recommended"] and not r["downloaded"]]
    heavy = [r for r in rows if (r["key"] in wanted or r["group"] in wanted) and not r["recommended"]]
    if heavy and not args.yes:
        names = ", ".join(r["key"] for r in heavy)
        gb = f"{mem.total / machine.GB:.0f}"
        print(f"{names}: needs more memory while it works than this Mac ({gb} GB) can usually spare; Lantern starts "
              "it only when there is room. Add --yes to download it anyway.", file=sys.stderr)  # fmt: skip
        return 1
    if not wanted:
        print("the recommended models are downloaded")
        return 0
    if _daemon():
        protocol.call("download_models", {"models": wanted})
        while True:  # the daemon downloads in its own process; follow its progress
            dl = protocol.call("status").get("model_download") or {}
            if not dl.get("running"):
                print("\r" + " " * 60 + "\r", end="")
                if dl.get("error"):
                    print(f"error: {dl['error']}", file=sys.stderr)
                    return 1
                print("downloaded")
                return 0
            total = dl.get("size_mb") or dl.get("approx_mb") or 0
            print(f"\r  {dl.get('key', '')} {dl.get('step', 1)}/{dl.get('total', 1)}: {dl.get('downloaded_mb', 0)} / "
                  f"{total} MB", end="", flush=True)  # fmt: skip
            time.sleep(1)
    env = {k: v for k, v in os.environ.items() if k not in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")}
    return subprocess.run([sys.executable, "-m", "lantern.models", *wanted], env=env).returncode


def cmd_rebuild(args: argparse.Namespace) -> int:
    """Start again with an empty index (as the menu's Rebuild): the folders stay, everything is indexed again."""
    if not args.yes:
        print("This deletes the index and indexes every folder again (your files are not touched). "
              "Run `lantern rebuild --yes` to do it.", file=sys.stderr)  # fmt: skip
        return 1
    if _daemon():
        out = protocol.call("rebuild_index", timeout=600)
        print(f"rebuilt: {len(out['roots'])} folders are being indexed again")
        return 0
    import sqlite3

    roots: list[str] = []
    if config.db_path().exists():
        try:
            old = sqlite3.connect(f"file:{config.db_path()}?mode=ro", uri=True)
            roots = [r[0] for r in old.execute("SELECT path FROM roots")]
            old.close()
        except sqlite3.DatabaseError:
            roots = []
    for suffix in ("", "-wal", "-shm"):
        Path(str(config.db_path()) + suffix).unlink(missing_ok=True)
    code = 0
    for root in roots:
        code |= cmd_index(argparse.Namespace(path=root, force=False, no_transcribe=False, dry_run=False))
    print(f"rebuilt: {len(roots)} folders indexed again" if roots else "rebuilt an empty index (no folders were known)")
    return code


def cmd_languages(args: argparse.Namespace) -> int:
    """Languages for app names and text in images: the macOS ones plus the ones added here (Settings → General)."""
    from lantern.service import settings as settings_mod
    from lantern.syslang import normalize_language, preferred_languages, system_languages

    if args.action in ("add", "remove") and not args.code:
        print("error: missing language code (e.g. de, zh-Hans)", file=sys.stderr)
        return 1
    if _daemon():
        params = {"action": args.action, "language": args.code} if args.action != "list" else {}
        try:
            out = protocol.call("languages", params)
        except protocol.DaemonError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    else:
        s = settings_mod.load()
        if args.action != "list":
            code = normalize_language(args.code)
            if code is None:
                print(f"error: {args.code!r} is not a language code", file=sys.stderr)
                return 1
            extra = [c for c in s.extra_languages if c != code]
            s.extra_languages = [*extra, code] if args.action == "add" else extra
            settings_mod.save(s)
        out = {"system": list(system_languages()), "extra": list(s.extra_languages),
               "effective": preferred_languages(s.extra_languages)}  # fmt: skip
    if args.json:
        return _print_json(out)
    print("macOS: " + (", ".join(out["system"]) or "-"))
    print("added: " + (", ".join(out["extra"]) or "-"))
    print("used:  " + ", ".join(out["effective"]))
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
    parser = argparse.ArgumentParser(
        prog="lantern",
        description="Local, offline semantic file search.",
        epilog="Manual: man lantern (after ./install.sh). Full reference: docs/CLI.md in the repository.",
    )
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
        choices=["apps", "settings", "shortcuts", "code", "docs", "pdf", "photos", "media", "music"],
        help="only one kind of result (the panel's filter pills)",
    )
    p.set_defaults(func=cmd_search)

    p.add_argument("--json", action="store_true", help="print the whole reply as JSON (sections, scores, dates)")

    p = sub.add_parser("open", help="open result N of the last search (and record the choice)")
    p.add_argument("number", type=int)
    p.add_argument("--reveal", action="store_true", help="show in Finder instead of opening")
    p.add_argument("--print", action="store_true", help="only print the path")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("status", help="show daemon, index and queue status")
    p.add_argument("--json", action="store_true", help="print the status as JSON")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("diagnostics", help="print a report for a bug report (no file names or contents)")
    p.set_defaults(func=cmd_diagnostics)

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
    p.add_argument("--json", action="store_true", help="print as JSON")
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
    p.add_argument("--json", action="store_true", help="print all settings as JSON")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("models", help="list the models or download them (as Settings → Performance)")
    p.add_argument("action", nargs="?", choices=["list", "download"], default="list")
    p.add_argument("names", nargs="*", help="download: model keys or groups (required, whisper, photos)")
    p.add_argument("--yes", action="store_true", help="download a model this Mac's memory does not recommend")
    p.add_argument("--json", action="store_true", help="list as JSON")
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("rebuild", help="delete the index and index every folder again (files are not touched)")
    p.add_argument("--yes", action="store_true", help="do it (without it, only explains)")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("languages", help="languages for app names and text in images (macOS + added)")
    p.add_argument("action", nargs="?", choices=["list", "add", "remove"], default="list")
    p.add_argument("code", nargs="?", help="a language code such as de or zh-Hans")
    p.add_argument("--json", action="store_true", help="print as JSON")
    p.set_defaults(func=cmd_languages)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

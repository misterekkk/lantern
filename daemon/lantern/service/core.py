"""The Lantern daemon: API server, scans, watcher, approval flow, power policy and the index worker.

Threads: the API server (one thread per request), the control loop (scans after file events, power checks,
starting/stopping the worker) and a reader for the worker's progress. The text model stays loaded for fast
searches (~455 MB); indexing runs in a separate worker process (service/worker.py) that exits when the queue
is empty, so heavy models never stay resident.
"""

import ctypes
import json
import os
import signal
import socketserver
import struct
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from dataclasses import asdict
from pathlib import Path

from lantern import config
from lantern.api import protocol
from lantern.scanner.diff import MassDeletionRefused, RootUnavailable, root_available, scan_root
from lantern.search.hybrid import clear_history, mark_opened, search
from lantern.service import notify as notify_mod
from lantern.service import power as power_mod
from lantern.service import qos
from lantern.service import settings as settings_mod
from lantern.service import tree as tree_mod
from lantern.service.perf import PerfMonitor
from lantern.store import db

TICK_S = 0.5
QUERY_MODEL_IDLE_S = 300.0  # the second photo model's text encoder is released after this much inactivity
POWER_CHECK_S = 30.0
WORKER_RETRY_S = 30.0  # after a worker crash with work left


def footprint_mb() -> float:
    """Physical footprint of this process (what Activity Monitor shows as Memory)."""
    try:
        lib = ctypes.CDLL("/usr/lib/libproc.dylib")
        buf = ctypes.create_string_buffer(4096)
        if lib.proc_pid_rusage(os.getpid(), 4, buf) == 0:
            return struct.unpack_from("<Q", buf, 72)[0] / 2**20  # rusage_info_v4.ri_phys_footprint
    except OSError:
        pass
    return 0.0


def logs_dir() -> Path:
    return config.lantern_home() / "logs"


class Daemon:
    def __init__(self, embedder=None, watch: bool = True, power_reader=power_mod.read, worker_env: dict | None = None):
        self.settings = settings_mod.load()
        self.embedder = embedder
        self.watch_enabled = watch
        self.power_reader = power_reader
        self.worker_env = worker_env or {}
        self.lock = threading.RLock()  # daemon state
        self.scan_lock = threading.Lock()  # scans and maintenance (one writer besides the worker)
        # MLX streams belong to the thread that created them: load and run the model on one dedicated thread
        # (this also serialises model calls from concurrent API requests).
        self.model_thread = ThreadPoolExecutor(  # searches run here: keep them responsive (qos.py)
            max_workers=1,
            thread_name_prefix="lantern-model",
            initializer=qos.set_thread_qos,
            initargs=("user_initiated",),
        )
        self.query_embedder = None  # SigLIP 2 text side (photos), loaded on demand on the model thread
        self.query_embedder_used = 0.0
        self.has_images = False
        self.approved = False
        self.awaiting_approval = False
        self.manual_pause = False
        self.auto_pause: str | None = None
        self.worker: subprocess.Popen | None = None
        self.worker_terminating = False
        self.worker_retry_at = 0.0
        self.progress: dict = {}
        self.perf = PerfMonitor()
        self.last_run: dict = {}
        self.warnings: list[str] = []
        self.stopping = threading.Event()
        self.watcher = None
        self.server: socketserver.ThreadingUnixStreamServer | None = None
        self._last_power_check = 0.0
        self.power_state = power_mod.PowerState()
        # Started by the menu bar app: exit if the app goes away (crash, force quit) instead of staying orphaned.
        self.parent_pid = os.getppid() if os.environ.get("LANTERN_EXIT_WITH_PARENT") == "1" else None

    # ---------- helpers ----------

    def _conn(self):
        return db.connect(config.db_path())

    def _warn(self, message: str) -> None:
        with self.lock:
            self.warnings = (self.warnings + [f"{time.strftime('%H:%M:%S')} {message}"])[-20:]

    def _notify(self, message: str) -> None:
        # LANTERN_NOTIFY=0: a client (the menu bar app) shows its own notifications.
        if self.settings.notify and os.environ.get("LANTERN_NOTIFY") != "0":
            notify_mod.notify("Lantern", message)

    def _queued(self, conn) -> dict[str, int]:
        rows = conn.execute(
            "SELECT j.kind AS job, f.kind AS file, COUNT(*) AS n FROM jobs j JOIN files f ON f.id = j.file_id "
            "WHERE j.status = 'queued' GROUP BY j.kind, f.kind"
        ).fetchall()
        out: dict[str, int] = {}
        for r in rows:
            key = "transcribe" if r["job"] == "transcribe" else r["file"]
            out[key] = out.get(key, 0) + r["n"]
        return out

    def _roots(self, conn) -> list[str]:
        return [r[0] for r in conn.execute("SELECT path FROM roots ORDER BY path")]

    # ---------- scanning and approval ----------

    def _after_scan(self, conn, found_new: bool) -> None:
        if not found_new or not sum(self._queued(conn).values()):
            return
        with self.lock:
            if self.settings.index_mode == "auto":
                self.approved = True
            elif not self.approved:
                newly = not self.awaiting_approval
                self.awaiting_approval = True
                if newly:
                    n = sum(self._queued(conn).values())
                    self._notify(f"{n} new or changed files found. Run `lantern approve` to index them.")

    def scan(self, root: str, force: bool = False) -> dict:
        with self.scan_lock:
            conn = self._conn()
            try:
                result = scan_root(conn, Path(root), force=force, **settings_mod.walk_options(self.settings))
            except RootUnavailable as exc:
                self._warn(f"{exc}; nothing removed")
                return {"root": root, "error": "unavailable"}
            except MassDeletionRefused as exc:
                self._warn(f"{root}: {exc}")
                self._notify(
                    f"{exc.missing} files missing in {Path(root).name}; nothing removed. Use `lantern index --force`."
                )
                return {"root": root, "error": "mass_deletion_refused", "missing": exc.missing, "total": exc.total}
            if result.unreadable:
                self._warn(
                    f"no access to {len(result.unreadable)} folder(s) in {root} (e.g. {result.unreadable[0]}); "
                    "their indexed files are kept. Grant access in System Settings > Privacy & Security."
                )
            if result.deleted and db.maybe_compact(conn):
                self._warn(f"compacted the index after removing {len(result.deleted)} files")
            summary = {
                "root": root, "new": len(result.new), "changed": len(result.changed), "moved": len(result.moved),
                "removed": len(result.deleted), "unchanged": result.unchanged + len(result.touched),
                "unreadable": result.unreadable[:20], "cloud_only": result.cloud_only,
            }  # fmt: skip
            self._after_scan(conn, bool(result.new or result.changed))
            conn.close()
            return summary

    def scan_all(self) -> list[dict]:
        conn = self._conn()
        roots = self._roots(conn)
        conn.close()
        return [self.scan(r) for r in roots]

    # ---------- worker ----------

    @contextmanager
    def worker_suspended(self):
        """Stop the worker (current file finishes) for maintenance; the control loop restarts it afterwards."""
        with self.lock:
            self.manual_pause, saved = True, self.manual_pause
        self._stop_worker(wait=True)
        try:
            yield
        finally:
            with self.lock:
                self.manual_pause = saved

    def _paused(self) -> str | None:
        if self.manual_pause:
            return "manual"
        return self.auto_pause

    def _start_worker(self) -> None:
        logs_dir().mkdir(parents=True, exist_ok=True)
        log = open(logs_dir() / "worker.log", "ab")  # noqa: SIM115 - owned by the child
        env = {**os.environ, **self.worker_env}
        proc = subprocess.Popen(
            [sys.executable, "-m", "lantern.service.worker"],
            stdout=subprocess.PIPE, stderr=log, env=env, text=True, start_new_session=True,
        )  # fmt: skip
        log.close()
        with self.lock:
            self.worker, self.worker_terminating, self.progress = proc, False, {"stage": "starting"}
        threading.Thread(target=self._read_worker, args=(proc,), daemon=True).start()

    def _read_worker(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self.lock:
                if event.get("event") == "progress":
                    self.progress = event
                elif event.get("event") == "done":
                    self.last_run = {**event, "finished": time.strftime("%Y-%m-%d %H:%M:%S")}

    def _stop_worker(self, wait: bool = False) -> None:
        with self.lock:
            proc = self.worker
            if proc is None:
                return
            if not self.worker_terminating and proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                self.worker_terminating = True
        if wait:
            try:
                proc.wait(timeout=600)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            self._reap_worker()

    def _reap_worker(self) -> None:
        with self.lock:
            proc = self.worker
            if proc is None or proc.poll() is None:
                return
            self.worker, self.progress = None, {}
            code = proc.returncode
        conn = self._conn()
        left = sum(self._queued(conn).values())
        conn.close()
        with self.lock:
            if code != 0 and not self.worker_terminating:
                self._warn(f"index worker exited with code {code}; see logs/worker.log")
                self.worker_retry_at = time.monotonic() + WORKER_RETRY_S
            if left == 0 and self.settings.index_mode == "ask":
                self.approved = False

    def _manage_worker(self) -> None:
        self._reap_worker()
        with self.lock:
            running = self.worker is not None
            paused = self._paused() or self.stopping.is_set()
            want = self.approved or self.settings.index_mode == "auto"
        if running and paused:
            self._stop_worker()
            return
        if running or paused or not want or time.monotonic() < self.worker_retry_at:
            return
        conn = self._conn()
        has_work = sum(self._queued(conn).values()) > 0
        conn.close()
        if has_work:
            self._start_worker()

    def _release_idle_query_model(self) -> None:
        q = self.query_embedder
        if (
            q is not None
            and getattr(q, "loaded", False)
            and time.monotonic() - self.query_embedder_used > QUERY_MODEL_IDLE_S
        ):
            self.model_thread.submit(q.unload)

    # ---------- power ----------

    def _check_power(self) -> None:
        now = time.monotonic()
        if now - self._last_power_check < POWER_CHECK_S:
            return
        self._last_power_check = now
        state = self.power_reader()
        reason = None
        if self.settings.pause_on_battery and state.on_battery:
            reason = "battery"
        elif self.settings.pause_on_low_power and state.low_power:
            reason = "low_power"
        elif self.settings.pause_on_thermal and state.thermal_pressure:
            reason = "thermal"
        with self.lock:
            self.auto_pause = reason
            self.power_state = state

    def _scan_all_soon(self) -> None:
        def scan() -> None:
            qos.set_thread_qos("utility")  # housekeeping, not something the user waits for
            self.scan_all()

        threading.Thread(target=scan, daemon=True).start()

    # ---------- API ----------

    def handle(self, method: str, params: dict) -> dict:
        handler = getattr(self, f"api_{method.replace('.', '_')}", None)
        if handler is None:
            raise ValueError(f"unknown method {method!r}")
        return handler(**params)

    def api_ping(self) -> dict:
        return {"ok": True, "pid": os.getpid()}

    def api_status(self) -> dict:
        conn = self._conn()
        try:
            files = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM files GROUP BY status")}
            failed = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'failed'").fetchone()[0]
            roots = [
                {"path": r["path"], "last_scan": r["last_scan"], "available": root_available(Path(r["path"]))}
                for r in conn.execute("SELECT path, last_scan FROM roots ORDER BY path")
            ]
            queued = self._queued(conn)
            chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            kinds = {
                r[0]: r[1]
                for r in conn.execute("SELECT kind, COUNT(*) FROM files WHERE status = 'indexed' GROUP BY kind")
            }
        finally:
            conn.close()
        with self.lock:
            worker = {"running": self.worker is not None, **self.progress} if self.worker else {"running": False}
            return {
                "pid": os.getpid(), "memory_mb": round(footprint_mb()), "mode": self.settings.index_mode,
                "approved": self.approved, "awaiting_approval": self.awaiting_approval,
                "paused": self._paused(), "power": asdict(self.power_state),
                "worker": worker, "queued": queued, "failed_jobs": failed,
                "files": files, "kinds": kinds, "chunks": chunks, "roots": roots, "last_run": self.last_run,
                "photo_model_loaded": bool(getattr(self.query_embedder, "loaded", False)),
                "warnings": self.warnings[-5:],
            }  # fmt: skip

    def _refresh_has_images(self, conn) -> None:
        self.has_images = (
            conn.execute("SELECT 1 FROM chunks WHERE kind IN ('image', 'frame') LIMIT 1").fetchone() is not None
        )

    def _photo_model(self):
        """Model thread only: the second photo model's text encoder, loaded when photos are indexed (~0.33 s)."""
        if not self.has_images or self.query_embedder is None:
            return None
        self.query_embedder.load()
        self.query_embedder_used = time.monotonic()
        return self.query_embedder

    def _search(self, query: str, limit: int, log: bool, lang: str, category: str | None = None):
        conn = self._conn()
        try:
            self._refresh_has_images(conn)
            return search(conn, self.embedder, query, limit, log=log, lang=lang, query_embedder=self._photo_model(),
                          category=category)  # fmt: skip
        finally:
            conn.close()

    def api_warmup(self) -> dict:
        """The search panel opened: load the photo query model now, while the user types."""

        def warm():
            conn = self._conn()
            try:
                self._refresh_has_images(conn)
            finally:
                conn.close()
            return self._photo_model() is not None

        return {"photo_model": self.model_thread.submit(warm).result()}

    def api_search(
        self, query: str, limit: int = 10, log: bool = True, lang: str | None = None, category: str | None = None
    ) -> dict:
        from lantern.syslang import system_language

        resp = self.model_thread.submit(self._search, query, limit, log, lang or system_language(), category).result()
        return {
            "results": [asdict(r) for r in resp.results], "event_id": resp.event_id,
            "vector_ms": resp.vector_ms, "keyword_ms": resp.keyword_ms, "image_ms": resp.image_ms,
            "photos": [asdict(r) for r in resp.photos], "photos_first": resp.photos_first,
            "definitions": [asdict(d) for d in resp.definitions], "symbol_ms": resp.symbol_ms,
            "categories": resp.categories, "category": resp.category, "completion": resp.completion,
        }  # fmt: skip

    def api_opened(self, event_id: int, chunk_id: int) -> dict:
        """Record which result of a search the user opened (training data for a future reranker)."""
        conn = self._conn()
        try:
            mark_opened(conn, int(event_id), int(chunk_id))
        finally:
            conn.close()
        return {"ok": True}

    def api_index(self, path: str, force: bool = False) -> dict:
        root = str(Path(path).expanduser().resolve())
        if not root_available(Path(root)):
            raise FileNotFoundError(f"folder not available: {root}")
        summary = self.scan(root, force=force)
        with self.lock:
            self.approved, self.awaiting_approval = True, False  # explicit request = approval
        if self.watcher:
            self.watcher.watch(root)
        return summary

    def api_remove(self, path: str) -> dict:
        root = str(Path(path).expanduser().resolve())
        with self.worker_suspended(), self.scan_lock:
            conn = self._conn()
            row = conn.execute("SELECT id FROM roots WHERE path = ?", (root,)).fetchone()
            if row is None:
                conn.close()
                raise KeyError(f"{root} is not an indexed folder")
            with db.transaction(conn):
                n = db.remove_root(conn, row["id"])
            db.compact(conn)
            conn.close()
        if self.watcher:
            self.watcher.unwatch(root)
        return {"root": root, "files": n}

    def api_scan(self) -> dict:
        return {"scans": self.scan_all()}

    def api_pending(self) -> dict:
        conn = self._conn()
        queued = self._queued(conn)
        conn.close()
        with self.lock:
            return {"queued": queued, "awaiting_approval": self.awaiting_approval, "approved": self.approved}

    def api_approve(self) -> dict:
        with self.lock:
            self.approved, self.awaiting_approval = True, False
        return self.api_pending()

    def api_decline(self) -> dict:
        with self.lock:
            self.awaiting_approval = False  # jobs stay queued; asked again after new changes or `scan`
        return self.api_pending()

    def api_pause(self) -> dict:
        with self.lock:
            self.manual_pause = True
        return {"paused": self._paused()}

    def api_resume(self) -> dict:
        with self.lock:
            self.manual_pause = False
        return {"paused": self._paused()}

    def api_config_get(self) -> dict:
        return asdict(self.settings)

    def api_config_set(self, key: str, value: str) -> dict:
        with self.lock:
            settings_mod.update(self.settings, key, str(value))
            settings_mod.save(self.settings)
            if key in ("pause_on_battery", "pause_on_low_power", "pause_on_thermal"):
                self._last_power_check = 0.0
        if key in ("index_photos", "index_audio_video"):  # files join or leave the index at the next scan: now
            self._scan_all_soon()
        return asdict(self.settings)

    def api_exclude(self, action: str, pattern: str) -> dict:
        with self.lock:
            fn = settings_mod.add_exclude if action == "add" else settings_mod.remove_exclude
            fn(self.settings, pattern)
            settings_mod.save(self.settings)
        self._scan_all_soon()  # apply now
        return {"exclude": list(self.settings.exclude)}

    def api_extensions(self, action: str, extension: str) -> dict:
        """Ignore a file extension again or stop ignoring it (`csv`, `.csv` and `*.csv` all work)."""
        with self.lock:
            fn = settings_mod.add_extension if action == "add" else settings_mod.remove_extension
            changed = fn(self.settings, extension)
            settings_mod.save(self.settings)
        self._scan_all_soon()  # apply now
        return {"changed": changed, "exclude_extensions": list(self.settings.exclude_extensions)}

    def api_extension_suggestions(self) -> dict:
        """Extensions for the settings field: every type Lantern indexes, with the number of indexed files."""
        from collections import Counter

        from lantern.extractors import known_extensions

        conn = self._conn()
        counts = Counter(Path(r[0]).suffix.lower() for r in conn.execute("SELECT path FROM files"))
        conn.close()
        known = known_extensions()
        items = [
            {"ext": e, "kind": known.get(e), "indexed": counts.get(e, 0)} for e in sorted(set(known) | set(counts))
        ]
        return {"suggestions": [i for i in items if i["ext"]], "ignored": list(self.settings.exclude_extensions)}

    def api_tree(self, path: str) -> dict:
        conn = self._conn()
        try:
            return tree_mod.list_children(conn, path, self._roots(conn), self.settings)
        finally:
            conn.close()

    def api_tree_search(self, query: str, limit: int = 100) -> dict:
        conn = self._conn()
        try:
            return tree_mod.search(conn, query, self._roots(conn), self.settings, limit=limit)
        finally:
            conn.close()

    def api_tree_preview(self, path: str) -> dict:
        conn = self._conn()
        try:
            return {"path": path, "indexed": tree_mod.removal_preview(conn, path)}
        finally:
            conn.close()

    def api_tree_set(self, path: str, included: bool) -> dict:
        with self.lock:
            tree_mod.set_included(self.settings, path, bool(included))
            settings_mod.save(self.settings)
        self._scan_all_soon()  # apply now
        return {"exclude": list(self.settings.exclude)}

    def api_preview(self, path: str, start_line: int, end_line: int | None = None, context: int = 12) -> dict:
        """Lines of an indexed text/code file around a result, for the panel's quick look (Space). Only files in
        the index can be read this way."""
        from lantern.extractors.text import SkipFile, extract_text

        conn = self._conn()
        row = conn.execute("SELECT kind FROM files WHERE path = ?", (path,)).fetchone()
        conn.close()
        if row is None or row["kind"] not in ("text", "code"):
            raise ValueError("not an indexed text or code file")
        try:
            lines = extract_text(Path(path)).split("\n")
        except (SkipFile, OSError) as exc:
            raise ValueError(f"cannot read the file: {exc}") from exc
        end_line = end_line or start_line
        first, last = max(1, start_line - context), min(len(lines), end_line + context)
        return {
            "path": path, "start_line": start_line, "end_line": end_line, "total_lines": len(lines),
            "lines": [[n, lines[n - 1]] for n in range(first, last + 1)],
        }  # fmt: skip

    def api_perf(self) -> dict:
        with self.lock:
            progress, worker = dict(self.progress), self.worker
        loaded = ["embeddinggemma"] if self.embedder is not None else []
        if getattr(self.query_embedder, "loaded", False):
            loaded.append("siglip_text")
        if worker is not None:
            self.perf.tick(worker.pid, progress)
        return self.perf.snapshot(progress, loaded, self.settings.index_video_frames)

    def api_compact(self) -> dict:
        with self.worker_suspended(), self.scan_lock:
            conn = self._conn()
            before = config.db_path().stat().st_size
            db.compact(conn)
            conn.close()
        return {"before": before, "after": config.db_path().stat().st_size}

    def api_clear_history(self) -> dict:
        with self.worker_suspended(), self.scan_lock:
            conn = self._conn()
            n = clear_history(conn)
            db.compact(conn)
            conn.close()
        return {"deleted": n}

    def api_shutdown(self) -> dict:
        self.stopping.set()
        return {"stopping": True}

    # ---------- lifecycle ----------

    def _serve(self, path: Path) -> None:
        daemon = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                msg_id = None
                try:
                    msg = protocol.read_message(self.request)
                    msg_id = msg.get("id")
                    result = daemon.handle(msg["method"], msg.get("params") or {})
                    protocol.write_message(self.request, {"id": msg_id, "result": result})
                except Exception as exc:  # report to the client; never crash the server
                    try:
                        error = {"type": type(exc).__name__, "message": str(exc)}
                        protocol.write_message(self.request, {"id": msg_id, "error": error})
                    except OSError:
                        pass

        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True

        old_umask = os.umask(0o077)  # socket created user-only (no window before chmod)
        try:
            self.server = Server(str(path), Handler)
        finally:
            os.umask(old_umask)
        os.chmod(path, 0o600)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _prepare_socket(self) -> Path:
        path = protocol.socket_path()
        home = config.lantern_home()
        home.mkdir(parents=True, exist_ok=True)
        os.chmod(home, 0o700)  # index, settings and the default socket: user-only
        path.parent.mkdir(parents=True, exist_ok=True)  # a custom LANTERN_SOCKET directory is left as it is
        if path.exists():
            if protocol.running():
                raise RuntimeError(f"another Lantern daemon is already running ({path})")
            path.unlink()  # stale socket from a crash
        return path

    def run(self) -> int:
        path = self._prepare_socket()
        if self.embedder is None:
            from lantern.service.worker import make_embedder

            self.embedder = make_embedder()
        if hasattr(self.embedder, "load"):
            self.model_thread.submit(self.embedder.load).result()  # warm model: first search is fast
        if self.query_embedder is None:
            from lantern.service.worker import make_query_embedder

            self.query_embedder = make_query_embedder()
        conn = self._conn()
        from lantern.queue import jobs

        with db.transaction(conn):
            jobs.recover(conn)  # a worker killed together with the previous daemon
        roots = self._roots(conn)
        conn.close()

        if self.watch_enabled:
            from lantern.service.watcher import RootWatcher

            self.watcher = RootWatcher()
            self.watcher.start()
            for root in roots:
                self.watcher.watch(root)
        self._check_power()
        self.scan_all()  # source of truth after any downtime
        conn = self._conn()
        self._after_scan(conn, found_new=True)  # also files left waiting by an earlier run
        conn.close()
        self._serve(path)
        qos.set_thread_qos("utility")  # this thread now only scans and supervises; API threads keep default QoS
        if threading.current_thread() is threading.main_thread():  # launchd stops agents with SIGTERM
            signal.signal(signal.SIGTERM, lambda *_: self.stopping.set())

        try:
            while not self.stopping.is_set():
                if self.parent_pid is not None and os.getppid() != self.parent_pid:
                    self._warn("parent app exited; stopping")
                    break
                if self.watcher:
                    for root in self.watcher.take_quiet(self.settings.debounce_s):
                        self.scan(root)
                self._check_power()
                self._manage_worker()
                self._release_idle_query_model()
                with self.lock:
                    worker_pid, progress = (self.worker.pid if self.worker else None), dict(self.progress)
                self.perf.tick(worker_pid, progress)
                self.stopping.wait(TICK_S)
        finally:
            self._stop_worker(wait=True)
            if self.watcher:
                self.watcher.stop()
            if self.server:
                self.server.shutdown()
                self.server.server_close()
            self.model_thread.shutdown(wait=False)
            with suppress(FileNotFoundError):
                path.unlink()
        return 0


def wait_for_socket(timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if protocol.running():
            return True
        time.sleep(0.1)
    return False

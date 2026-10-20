"""The Lantern daemon: API server, scans, watcher, approval flow, power policy and the index worker.

Threads: the API server (one thread per request), the control loop (scans after file events, power checks,
starting/stopping the worker) and a reader for the worker's progress. The text model stays loaded for fast
searches (~455 MB); indexing runs in a separate worker process (service/worker.py) that exits when the queue
is empty, so heavy models never stay resident.
"""

import ctypes
import json
import os
import shutil
import signal
import socketserver
import sqlite3
import struct
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from dataclasses import asdict, fields
from pathlib import Path

from lantern import config, filemeta
from lantern.api import protocol
from lantern.scanner import guard
from lantern.scanner.diff import MassDeletionRefused, RootUnavailable, root_available, scan_root
from lantern.search.hybrid import clear_history, learned_choice, mark_opened, search
from lantern.service import machine, qos
from lantern.service import notify as notify_mod
from lantern.service import power as power_mod
from lantern.service import settings as settings_mod
from lantern.service import tree as tree_mod
from lantern.service.perf import PerfMonitor
from lantern.store import db

TICK_S = 0.5
META_BATCH = 50  # files whose date and place are read per control-loop tick (filemeta.refresh)
META_IDLE_S = 5.0
PEOPLE_CHECK_S = 30.0  # how often an idle daemon looks for photos whose people are not counted yet
QUERY_MODEL_IDLE_S = 300.0  # the second photo model's text encoder is released after this much inactivity
POWER_CHECK_S = 30.0
MEMORY_CHECK_S = 5.0  # macOS's memory pressure and swap, read with sysctl (cheap)
MEMORY_CALM_S = 180.0  # this long without pressure before indexing resumes
MODEL_IDLE_S = 10 * 60.0  # "on demand" mode: the search model is released after this long without a search
# The app reports an open Settings window with every status poll (2 s); without a report for this long the window
# counts as closed (app quit or crashed), so a hold never outlives the app.
SETTINGS_HOLD_S = 30.0
APPS_CHECK_S = 120.0  # how often the app folders are checked for added, removed or updated apps
WORKER_RETRY_S = 30.0  # after a worker crash with work left
# A file the worker spends longer on is taken as stuck (a hung PDFKit/Vision call cannot be interrupted from Python):
# the worker is killed, that file is skipped and the queue goes on. Generous on purpose: a 1000-page scanned PDF or a
# three-hour recording may legitimately take long.
STUCK_FILE_S = {"index": 30 * 60.0, "transcribe": 90 * 60.0}
MIN_FREE_BYTES = 2 * 1024**3  # indexing pauses below this much free space on the index's disk (low_disk)


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


def delete_data() -> None:
    """The index, the settings and the logs (`delete_all_data`; the CLI without a daemon)."""
    for suffix in ("", "-wal", "-shm"):
        with suppress(FileNotFoundError):
            Path(str(config.db_path()) + suffix).unlink()
    with suppress(FileNotFoundError):
        settings_mod.path().unlink()
    shutil.rmtree(logs_dir(), ignore_errors=True)


class Daemon:
    def __init__(self, embedder=None, watch: bool = True, power_reader=power_mod.read, worker_env: dict | None = None,
                 memory_reader=None):  # fmt: skip
        self.settings = settings_mod.load()
        settings_problems = list(settings_mod.problems)
        self.memory_reader = memory_reader or (lambda: machine.read())  # looked up at call time (tests patch it)
        self.memory_pause: str | None = None  # "memory" while macOS is short of memory (phase 17)
        self._memory_calm_since: float | None = None
        self._last_memory_check = 0.0
        self.swap_watch = machine.SwapWatch()
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
        # While Settings are open, work the user just set up waits until they close (they may still exclude parts in
        # the folder tree): a folder added there, and in "auto" mode anything a rescan finds.
        self.settings_seen = 0.0  # monotonic time of the app's last "Settings are open" report
        self._apps_signature: tuple | None = None  # app folders as of the last refresh (None: refresh due)
        self._apps_checked = 0.0
        self.held_for_settings = False
        self.manual_pause = False
        self.auto_pause: str | None = None
        self.models_missing = False  # True on first launch of the prebuilt .dmg until the required model is downloaded
        self.downloader: subprocess.Popen | None = None
        self.download_state: dict | None = None
        self.worker: subprocess.Popen | None = None
        self.worker_terminating = False
        self.worker_retry_at = 0.0
        self._meta_check_at = 0.0  # next time filemeta.refresh looks for files without a date
        self.last_search_at = time.monotonic()
        self.index_problem: str | None = None  # "newer" (from a newer Lantern) | "damaged"
        self.model_reload_ms: int | None = None  # last reload of the search model in "on demand" mode
        self._people_check_at = 0.0  # next time it looks for photos without people counted
        self.progress: dict = {}
        self.progress_at = 0.0  # when the worker reported the file it is on
        self._whisper_checked = (-1e9, False)  # (when, Whisper downloaded)
        self.perf = PerfMonitor()
        self.last_run: dict = {}
        self.warnings: list[str] = []
        self.warning_events: list[dict] = []  # the same warnings with a code, for the app to word (_warn)
        for problem in settings_problems:  # a hand-edited config.toml: what was corrected (the file is fixed on save)
            self._warn(f"settings: {problem}", "settings_fixed", detail=problem)
        self.stopping = threading.Event()
        self.watcher = None
        self.server: socketserver.ThreadingUnixStreamServer | None = None
        self._last_power_check = 0.0
        self.power_state = power_mod.PowerState()
        # Started by the menu bar app: exit if the app goes away (crash, force quit) instead of staying orphaned.
        self.parent_pid = os.getppid() if os.environ.get("LANTERN_EXIT_WITH_PARENT") == "1" else None

    # ---------- helpers ----------

    def _conn(self):
        try:
            return db.connect(config.db_path())
        except db.NewerSchemaError:
            # Updated code upgraded the index under this process: stop, so the app starts the daemon again from the
            # new code (DaemonSupervisor restarts it), instead of failing every search until someone restarts it.
            self._warn("the index was upgraded by a newer Lantern; restarting", "schema_upgraded")
            self.stopping.set()
            raise

    def _warn(self, message: str, code: str = "", **args) -> None:
        """message: English, for the CLI and logs. code + args: the same warning for the app, which words it in its own
        language (LanternCore Format.warning); an unknown code falls back to the message."""
        stamp = time.strftime("%H:%M:%S")
        with self.lock:
            self.warnings = (self.warnings + [f"{stamp} {message}"])[-20:]
            event = {"time": stamp, "code": code, "args": {k: str(v) for k, v in args.items()}, "message": message}
            self.warning_events = (self.warning_events + [event])[-20:]

    def _notify(self, message: str) -> None:
        # LANTERN_NOTIFY=0: a client (the menu bar app) shows its own notifications.
        if self.settings.notify and os.environ.get("LANTERN_NOTIFY") != "0":
            notify_mod.notify("Lantern", message)

    def _queued(self, conn) -> dict[str, int]:
        """Work the worker can do now. Transcripts queued before transcription was switched off (or Whisper removed)
        wait without counting: otherwise the worker would be started, skip them and exit, again and again."""
        rows = conn.execute(
            "SELECT j.kind AS job, f.kind AS file, COUNT(*) AS n FROM jobs j JOIN files f ON f.id = j.file_id "
            "WHERE j.status = 'queued' GROUP BY j.kind, f.kind"
        ).fetchall()
        out: dict[str, int] = {}
        for r in rows:
            if r["job"] == "transcribe" and not (self._transcription_on() and self._whisper_fits()):
                continue
            key = "transcribe" if r["job"] == "transcribe" else r["file"]
            out[key] = out.get(key, 0) + r["n"]
        return out

    def _whisper_fits(self) -> bool:
        """Whisper (~1.8 GB) only while the Mac has room for it now: a 24 GB Mac busy training a model waits too."""
        if "fake" in (self.worker_env.get("LANTERN_EMBEDDER"), os.environ.get("LANTERN_EMBEDDER")):
            return True
        return machine.fits("whisper", self.memory_reader())

    def _transcription_on(self) -> bool:
        if not (self.settings.index_audio_video and self.settings.transcribe):
            return False
        if "fake" in (self.worker_env.get("LANTERN_EMBEDDER"), os.environ.get("LANTERN_EMBEDDER")):
            return True  # tests: the worker uses a fake transcriber
        now = time.monotonic()
        if now - self._whisper_checked[0] > 30:  # a cheap local lookup, not repeated on every control-loop tick
            from lantern.transcription import whisper

            self._whisper_checked = (now, whisper.available())
        return self._whisper_checked[1]

    def _roots(self, conn) -> list[str]:
        return [r[0] for r in conn.execute("SELECT path FROM roots ORDER BY path")]

    # ---------- scanning and approval ----------

    def _after_scan(self, conn, found_new: bool) -> None:
        if not found_new or not sum(self._queued(conn).values()):
            return
        with self.lock:
            if self.settings.index_mode == "auto" and self._settings_open():
                self.held_for_settings = True
            elif self.settings.index_mode == "auto":
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
                self._warn(f"{exc}; nothing removed", "root_unavailable", root=root)
                return {"root": root, "error": "unavailable"}
            except MassDeletionRefused as exc:
                self._warn(f"{root}: {exc}", "mass_deletion", root=root, missing=exc.missing, total=exc.total)
                self._notify(
                    f"{exc.missing} files missing in {Path(root).name}; nothing removed. Use `lantern index --force`."
                )
                return {"root": root, "error": "mass_deletion_refused", "missing": exc.missing, "total": exc.total}
            if result.unreadable:
                self._warn(
                    f"no access to {len(result.unreadable)} folder(s) in {root} (e.g. {result.unreadable[0]}); "
                    "their indexed files are kept. Grant access in System Settings > Privacy & Security.",
                    "no_access", count=len(result.unreadable), root=root,
                )
            if result.deleted and db.maybe_compact(conn):
                self._warn(f"compacted the index after removing {len(result.deleted)} files", "compacted",
                           count=len(result.deleted))  # fmt: skip
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
        from lantern import thumbnails

        thumbnails.prune(max_age_s=0)  # thumbnails of a previous run (Raycast extension) never outlive it
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

    def _settings_open(self) -> bool:
        return time.monotonic() - self.settings_seen < SETTINGS_HOLD_S

    def _holding(self) -> bool:
        """True while work waits for Settings to close; releases (= approves) it once they are closed."""
        if self.held_for_settings and not self._settings_open():
            self.held_for_settings = False
            self.approved, self.awaiting_approval = True, False
        return self.held_for_settings

    def _paused(self) -> str | None:
        if self.manual_pause:
            return "manual"
        return self.auto_pause or self.memory_pause

    def _check_memory(self) -> None:
        """The Mac really short of memory: the worker finishes its file and waits; "critical": it is stopped at once and
        the photo query model released. Resumes after MEMORY_CALM_S of calm, so it does not start and stop every few
        seconds. "warn" alone does not pause: a busy 16 GB Mac (a browser, an editor, chat apps) sits at "warn" for
        hours while macOS compresses idle memory, so only heavy work waits then (machine.fits); indexing pauses when
        "warn" comes with fast-growing swap (the Mac is thrashing) or at "critical"."""
        now = time.monotonic()
        if now - self._last_memory_check < MEMORY_CHECK_S:
            return
        self._last_memory_check = now
        mem = self.memory_reader()
        swapping = self.swap_watch.growing(mem, now)
        short = mem.pressure >= machine.PRESSURE_CRITICAL or (mem.pressure >= machine.PRESSURE_WARN and swapping)
        with self.lock:
            was = self.memory_pause
            if short:
                self.memory_pause, self._memory_calm_since = "memory", None
            elif was:
                self._memory_calm_since = self._memory_calm_since or now
                if now - self._memory_calm_since >= MEMORY_CALM_S:
                    self.memory_pause, self._memory_calm_since = None, None
            worker = self.worker
        if short and not was:
            self._warn("indexing paused: the Mac is short of memory; it resumes by itself", "memory_pause")
        if mem.pressure >= machine.PRESSURE_CRITICAL:
            if worker is not None and worker.poll() is None:
                worker.kill()  # no time to finish the file: its transaction rolls back, the job is queued again
                with suppress(subprocess.TimeoutExpired):
                    worker.wait(timeout=5)
                with self.lock:
                    self.worker_terminating = True
                self._reap_worker()
            q = self.query_embedder
            if q is not None and getattr(q, "loaded", False):
                self.model_thread.submit(q.unload)

    def _start_worker(self, people_only: bool = False) -> None:
        logs_dir().mkdir(parents=True, exist_ok=True)
        log = open(logs_dir() / "worker.log", "ab")  # noqa: SIM115 - owned by the child
        env = {**os.environ, **self.worker_env}
        proc = subprocess.Popen(
            [sys.executable, "-m", "lantern.service.worker", *(["--people-only"] if people_only else [])],
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
                    same_file = (event.get("path"), event.get("stage")) == (
                        self.progress.get("path"),
                        self.progress.get("stage"),
                    )
                    if not same_file:
                        self.progress_at = time.monotonic()
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
                self._warn(f"index worker exited with code {code}; see logs/worker.log", "worker_exited", code=code)
                self.worker_retry_at = time.monotonic() + WORKER_RETRY_S
            if left == 0 and self.settings.index_mode == "ask":
                self.approved = False

    def _check_stuck_worker(self) -> None:
        with self.lock:
            proc, progress, since = self.worker, dict(self.progress), self.progress_at
        stage, path = progress.get("stage"), progress.get("path")
        limit = STUCK_FILE_S.get(stage or "")
        if proc is None or proc.poll() is not None or not path or limit is None or time.monotonic() - since < limit:
            return
        from lantern.queue import jobs

        proc.kill()  # SIGTERM would wait for the stuck file to finish
        proc.wait()
        conn = self._conn()
        try:
            with db.transaction(conn):
                jobs.give_up_file(conn, path, stage, f"took longer than {int(limit // 60)} min ({stage}); skipped")
        finally:
            conn.close()
        self._warn(f"skipped a file that took longer than {int(limit // 60)} min: {path}", "stuck_file",
                   minutes=int(limit // 60), path=path)  # fmt: skip
        with self.lock:
            self.worker_terminating = True  # an expected exit: no crash warning, no retry pause
        self._reap_worker()

    def _manage_worker(self) -> None:
        self._check_stuck_worker()
        self._reap_worker()
        with self.lock:
            running = self.worker is not None
            paused = self._paused() or self.stopping.is_set() or self.models_missing
            holding = self._holding()
            want = (self.approved or self.settings.index_mode == "auto") and not holding
        if running and (paused or holding):
            self._stop_worker()
            return
        if running or paused or holding or time.monotonic() < self.worker_retry_at:
            return
        conn = self._conn()
        has_work = want and sum(self._queued(conn).values()) > 0
        conn.close()
        if has_work:
            self._start_worker()
        elif self._people_waiting():  # needs no approval: these photos are already in the index
            # photos indexed before (or whose analysis is out of date): counted in a worker that does nothing else, so
            # files waiting for approval in "ask" mode stay waiting
            self._start_worker(people_only=True)

    def _people_waiting(self) -> bool:
        if not (self.settings.people and self.settings.index_photos) or time.monotonic() < self._people_check_at:
            return False
        from lantern import people

        conn = self._conn()
        try:
            n = people.pending(conn)
        finally:
            conn.close()
        self._people_check_at = time.monotonic() + PEOPLE_CHECK_S
        return n > 0

    def _release_idle_search_model(self) -> None:
        """"On demand" mode: the search model (~0.5 GB) is released after MODEL_IDLE_S without a search; apps and
        settings still answer without it, and the next search by meaning loads it again (time in status)."""
        e = self.embedder
        if (self.settings.mode == "on_demand" and e is not None and getattr(e, "loaded", False)
                and time.monotonic() - self.last_search_at > MODEL_IDLE_S):  # fmt: skip
            self.model_thread.submit(e.unload)

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
        elif _free_bytes(config.lantern_home()) < MIN_FREE_BYTES:
            reason = "low_disk"  # an index (and the WAL, VACUUM's copy) needs room; a full disk breaks the Mac too
        elif not settings_mod.in_schedule(self.settings, time.localtime().tm_hour, state.idle_s):
            reason = "schedule"  # indexing waits for the night or for the Mac to be idle; search works as always
        with self.lock:
            newly_low = reason == "low_disk" and self.auto_pause != "low_disk"
            self.auto_pause = reason
            self.power_state = state
        if newly_low:
            self._warn("indexing paused: less than 2 GB free on the disk of the index", "low_disk")

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

    def _download_status(self) -> dict | None:
        state = self.download_state
        if state is None:
            return None
        out = dict(state)
        repo = out.get("repo")
        if out.get("running") and repo:
            from lantern import models as models_mod

            out["downloaded_mb"] = models_mod.blob_bytes(repo) // 10**6  # decimal MB, like size_mb and Finder
        return out

    def api_status(self, settings_open: bool | None = None) -> dict:
        if settings_open is not None:  # only the app reports it (CLI calls leave it alone)
            with self.lock:
                self.settings_seen = time.monotonic() if settings_open else 0.0
                self._holding()
        if self.index_problem:  # the index cannot be read: what the app needs to explain it, nothing from the file
            return {
                "pid": os.getpid(), "memory_mb": round(footprint_mb()), "mode": self.settings.index_mode,
                "approved": False, "awaiting_approval": False, "paused": None, "worker": {"running": False},
                "queued": {}, "failed_jobs": 0, "files": {}, "kinds": {}, "chunks": 0, "roots": [], "last_run": None,
                "warnings": self.warnings[-5:], "warning_events": self.warning_events[-5:],
                "index_problem": self.index_problem,
                "models_missing": self.models_missing, "model_download": self._download_status(),
            }  # fmt: skip
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
            from lantern import people

            enriching = {"dates_pending": filemeta.pending(conn), "people_pending": people.pending(conn)}
        finally:
            conn.close()
        from lantern.transcription import whisper

        with self.lock:
            worker = {"running": self.worker is not None, **self.progress} if self.worker else {"running": False}
            return {
                "pid": os.getpid(), "memory_mb": round(footprint_mb()), "mode": self.settings.index_mode,
                "approved": self.approved, "awaiting_approval": self.awaiting_approval,
                "waiting_for_settings": self.held_for_settings,
                "paused": self._paused(), "power": asdict(self.power_state),
                "worker": worker, "queued": queued, "failed_jobs": failed,
                "files": files, "kinds": kinds, "chunks": chunks, "roots": roots, "last_run": self.last_run,
                **enriching,  # files whose date/place, photos whose people are still to be read (phase 16)
                "search_mode": self.settings.mode, "model_loaded": bool(getattr(self.embedder, "loaded", True)),
                "model_reload_ms": self.model_reload_ms,
                "photo_model_loaded": bool(getattr(self.query_embedder, "loaded", False)),
                "whisper_downloaded": whisper.available(),
                "models_missing": self.models_missing, "model_download": self._download_status(),
                "warnings": self.warnings[-5:], "warning_events": self.warning_events[-5:],
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

    def _search(self, query: str, limit: int, log: bool, lang: str, category: str | None = None,
                spelling: dict | None = None, quick: bool = False):  # fmt: skip
        conn = self._conn()
        self.last_search_at = time.monotonic()
        if not quick and not self.models_missing and not getattr(self.embedder, "loaded", True):
            t = time.perf_counter()  # "on demand": the model was released; measure how long it takes back
            self.embedder.load()
            self.model_reload_ms = round((time.perf_counter() - t) * 1000)
        try:
            if quick or self.models_missing:  # no model: never waits for the photo model either
                return search(conn, self.embedder, query, limit, log=log, lang=lang, category=category, quick=True)
            self._refresh_has_images(conn)
            return search(conn, self.embedder, query, limit, log=log, lang=lang, query_embedder=self._photo_model(),
                          category=category, spelling=spelling)  # fmt: skip
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
        self,
        query: str,
        limit: int = 10,
        log: bool = True,
        lang: str | None = None,
        category: str | None = None,
        spelling: dict | None = None,
        quick: bool = False,
    ) -> dict:
        """spelling: {word: [guesses]} from the app's spell checker; the response's corrected_query says whether one
        was used (search/spelling.py)."""
        from lantern.syslang import system_language

        spelling = {str(w): [str(g) for g in gs][:8] for w, gs in (spelling or {}).items() if isinstance(gs, list)}
        if quick:  # no model involved: answered on this thread, not queued behind a running full search
            resp = self._search(query, limit, log, lang or system_language(), category, None, True)
        else:
            resp = self.model_thread.submit(
                self._search, query, limit, log, lang or system_language(), category, spelling or None
            ).result()
        return {
            "results": [asdict(r) for r in resp.results], "event_id": resp.event_id,
            "vector_ms": resp.vector_ms, "keyword_ms": resp.keyword_ms, "image_ms": resp.image_ms,
            "photos": [asdict(r) for r in resp.photos], "photos_first": resp.photos_first,
            "definitions": [asdict(d) for d in resp.definitions], "symbol_ms": resp.symbol_ms,
            "categories": resp.categories, "category": resp.category, "completion": resp.completion,
            "apps": [asdict(a) for a in resp.apps], "apps_first": resp.apps_first,
            "settings": [asdict(s) for s in resp.settings], "settings_first": resp.settings_first,
            "settings_app_name": resp.settings_app_name,
            "shortcuts": [asdict(s) for s in resp.shortcuts],
            "answer": asdict(resp.answer) if resp.answer else None,
            "definition": asdict(resp.definition) if resp.definition else None,
            "definition_first": resp.definition_first,
            "section_scores": resp.section_scores, "order": [name for name, _ in resp.sections()],
            "corrected_query": resp.corrected_query, "music": [asdict(m) for m in resp.music],
            "learned": self._learned(resp.corrected_query or query),
        }  # fmt: skip

    def api_import(self, source: str = "alfred", check: bool = False) -> dict:
        """Learned choices from another launcher (lantern/importers.py). check: only say whether there is one."""
        from lantern import importers

        if source != "alfred":
            raise ValueError(f"unknown source {source!r}")
        if check:
            return {"available": importers.ALFRED_DB.is_file()}
        conn = self._conn()
        try:
            return {"imported": importers.import_alfred(conn)}
        finally:
            conn.close()

    def _learned(self, query: str) -> str | None:
        conn = self._conn()
        try:
            return learned_choice(conn, query)
        finally:
            conn.close()

    def api_opened(self, event_id: int, chunk_id: int, key: str | None = None) -> dict:
        """Record which result of a search the user opened (learned choices; training data for a future reranker).
        key: the app's id of the row (apps, settings and songs all have chunk 0)."""
        conn = self._conn()
        try:
            mark_opened(conn, int(event_id), int(chunk_id), str(key)[:500] if key else None)
        finally:
            conn.close()
        return {"ok": True}

    def api_index(self, path: str, force: bool = False) -> dict:
        return self._index(path, force)

    def _index(self, path: str, force: bool = False, check: bool = True) -> dict:
        root = str(Path(path).expanduser().resolve())
        if not root_available(Path(root)):
            raise FileNotFoundError(f"folder not available: {root}")
        if check and not self._is_root(root):  # folders already indexed keep working; only new ones are checked
            guard.check(Path(root))
        summary = self.scan(root, force=force)
        with self.lock:
            if self._settings_open():  # added in Settings: starts when they close (the tree may still exclude parts)
                self.held_for_settings = True
            else:
                self.approved, self.awaiting_approval = True, False  # explicit request = approval
        if self.watcher:
            self.watcher.watch(root)
        return summary

    def _is_root(self, root: str) -> bool:
        conn = self._conn()
        try:
            return conn.execute("SELECT 1 FROM roots WHERE path = ?", (root,)).fetchone() is not None
        finally:
            conn.close()

    def api_estimate(self, path: str) -> dict:
        """What adding this folder would mean (files, size, rough time, refused or large), before it is added."""
        root = Path(path).expanduser().resolve()
        if not root_available(root):
            raise FileNotFoundError(f"folder not available: {root}")
        if self._is_root(str(root)):
            return {"path": str(root), "indexed": True}
        return guard.estimate(root, settings_mod.walk_options(self.settings)).to_dict() | {"indexed": False}

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
            if key in ("pause_on_battery", "pause_on_low_power", "pause_on_thermal", "schedule", "night_from",
                       "night_to"):
                self._last_power_check = 0.0
        if key in ("index_photos", "index_audio_video"):  # files join or leave the index at the next scan: now
            self._scan_all_soon()
        elif key == "transcribe" and self.settings.transcribe:
            self._enqueue_missing_transcripts()
        elif key == "places":
            conn = self._conn()
            try:
                if self.settings.places:
                    filemeta.read_places_again(conn)
                else:
                    filemeta.forget_places(conn)
            finally:
                conn.close()
            self._meta_check_at = 0.0
        elif key == "people":
            if self.settings.people:
                self._people_check_at = 0.0
            else:
                from lantern import people

                if self.worker is not None and self.progress.get("stage") == "people":
                    self._stop_worker(wait=True)  # it writes its last batch on the way out: before the deletion
                conn = self._conn()
                try:
                    people.forget(conn)
                finally:
                    conn.close()
        return asdict(self.settings)

    def _refresh_meta(self) -> None:
        """Dates and places of new files and of files indexed before phase 16, a small batch per tick (file headers
        only), paused like indexing."""
        if self._paused() or time.monotonic() < self._meta_check_at:
            return
        conn = self._conn()
        try:
            done = filemeta.refresh(conn, places=self.settings.places, limit=META_BATCH,
                                    should_stop=self.stopping.is_set)  # fmt: skip
        except Exception as exc:  # never stops the control loop; tried again later
            self._warn(f"reading file dates failed: {exc}", "dates_failed", detail=exc)
            done = 0
        finally:
            conn.close()
        if done < META_BATCH:  # nothing (more) to read: look again in a while (new files, a changed setting)
            self._meta_check_at = time.monotonic() + META_IDLE_S

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

    def api_music(self, songs: list | None = None, playlists: list | None = None, clear: bool = False,
                  source: str = "apple") -> dict:  # fmt: skip
        """A music library as the app reads it (MusicLibrary.swift, SpotifyLibrary.swift): replaces that source's
        list, vectors only for new or changed songs. clear: the source was switched off, forget its list."""
        if source not in ("apple", "spotify"):
            raise ValueError(f"unknown music source {source!r}")
        if self.models_missing and not clear:
            return {"songs": 0, "playlists": 0, "embedded": 0}
        from lantern import music as music_mod
        from lantern.embedding.base import format_document

        items = [] if clear else music_mod.items_from(songs or [], playlists or [], source)

        def store() -> int:
            conn = self._conn()
            try:
                model_id = db.ensure_model(conn, self.embedder.model_info())
                embed = lambda docs: self.embedder.embed_text([format_document(None, d) for d in docs])  # noqa: E731
                return music_mod.store_music(conn, items, embed, model_id, source)
            finally:
                conn.close()

        embedded = self.model_thread.submit(store).result()
        return {"songs": sum(i.kind == "song" for i in items), "playlists": sum(i.kind == "playlist" for i in items),
                "embedded": embedded}  # fmt: skip

    def api_languages(self, action: str | None = None, language: str | None = None) -> dict:
        """Preferred languages: the macOS ones (read fresh), the ones added in Settings, and the effective list."""
        from lantern.syslang import normalize_language, preferred_languages, system_languages

        if action in ("add", "remove"):
            code = normalize_language(language or "")
            if code is None:
                raise ValueError(f"{language!r} is not a language code")
            with self.lock:
                extra = [c for c in self.settings.extra_languages if c != code]
                self.settings.extra_languages = [*extra, code] if action == "add" else extra
                settings_mod.save(self.settings)
            self._languages_changed()
        system_languages.cache_clear()  # the user may have changed them in System Settings
        return {
            "system": list(system_languages()),
            "extra": list(self.settings.extra_languages),
            "effective": preferred_languages(self.settings.extra_languages),
        }

    def _languages_changed(self) -> None:
        """App names are read in the preferred languages: read them again."""
        self._apps_signature = None
        self._apps_checked = 0.0

    def _refresh_apps(self, force: bool = False) -> None:
        """Re-read the applications when their folders changed (or languages, or on start); vectors are computed on
        the model thread, only for new or changed apps."""
        from lantern import apps as apps_mod
        from lantern import system_settings
        from lantern.embedding.base import format_document
        from lantern.syslang import preferred_languages

        now = time.monotonic()
        if not force and now - self._apps_checked < APPS_CHECK_S:
            return
        self._apps_checked = now
        shortcuts = apps_mod.scan_shortcuts()  # cheap (~30 ms) and they change often: every check
        usage_conn = self._conn()
        try:  # how much each app is used (Spotlight's counts): name-match ties go to the used one
            apps_mod.refresh_usage([r[0] for r in usage_conn.execute("SELECT path FROM apps")])
        finally:
            usage_conn.close()
        conn = self._conn()
        try:
            apps_mod.store_shortcuts(conn, shortcuts)
        finally:
            conn.close()
        if self.models_missing:  # until the required model is downloaded, only shortcuts can be stored
            return
        signature = (apps_mod.folders_signature(), system_settings.signature())
        if not force and signature == self._apps_signature:
            return
        languages = preferred_languages(self.settings.extra_languages)
        found = apps_mod.scan_apps(languages)
        settings_items = system_settings.scan_settings(languages)

        def store() -> int:
            conn = self._conn()
            try:
                model_id = db.ensure_model(conn, self.embedder.model_info())
                embed = lambda docs: self.embedder.embed_text([format_document(None, d) for d in docs])  # noqa: E731
                n = apps_mod.store_apps(conn, found, languages, embed, model_id)
                return n + system_settings.store_settings(conn, settings_items, languages, embed, model_id)
            finally:
                conn.close()

        try:
            self.model_thread.submit(store).result()
            self._apps_signature = signature
        except Exception as exc:  # noqa: BLE001 - apps are an extra; never stop the daemon over them
            self._warn(f"could not read applications: {exc}", "apps_failed", detail=exc)

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

    def api_passage(self, chunk_id: int) -> dict:
        """The whole indexed passage of a result (the search reply carries only an excerpt), for previews in clients
        such as the Raycast extension. Text from the index only; the file itself is not read."""
        from lantern.search.basic import _transcript_at

        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT c.text, c.kind, c.page, c.start_line, c.end_line, c.start_ms, c.end_ms, f.path "
                "FROM chunks c JOIN files f ON f.id = c.file_id WHERE c.id = ?",
                (int(chunk_id),),
            ).fetchone()
            if row is None:
                raise ValueError("no such passage")
            out = dict(row)
            if out["text"] is None and out["start_ms"] is not None:
                out["text"] = _transcript_at(conn, int(chunk_id), out["start_ms"], out["end_ms"]) or None
        finally:
            conn.close()
        return out

    def _enqueue_missing_transcripts(self) -> None:
        """Queue transcription for already-indexed recordings when Whisper was downloaded or switched on later."""
        from lantern.queue import jobs
        from lantern.transcription import whisper

        if not (self.settings.index_audio_video and self.settings.transcribe and whisper.available()):
            return
        with self.scan_lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT f.id FROM files f WHERE f.status = 'indexed' AND f.kind IN ('audio', 'video') "
                    "AND EXISTS (SELECT 1 FROM chunks a WHERE a.file_id = f.id AND a.kind = 'audio') "
                    "AND NOT EXISTS (SELECT 1 FROM chunks t WHERE t.file_id = f.id AND t.kind = 'transcript')"
                ).fetchall()
                if rows:
                    with db.transaction(conn):
                        for (file_id,) in rows:
                            jobs.enqueue(conn, file_id, "transcribe")
                    self._after_scan(conn, found_new=True)
            finally:
                conn.close()

    def api_thumbnail(self, path: str, start_ms: int | None = None, size: int = 320) -> dict:
        """A small JPEG of an indexed photo, or of a video's frame at start_ms, for clients without access to the
        user's folders (the Raycast extension). Written to a user-only cache, deleted after 10 minutes
        (lantern/thumbnails.py); {"path": None} for other files."""
        from lantern import thumbnails

        conn = self._conn()
        row = conn.execute("SELECT kind FROM files WHERE path = ?", (path,)).fetchone()
        conn.close()
        if row is None:
            raise ValueError("not an indexed file")
        out = thumbnails.make(path, row["kind"], start_ms, size)
        return {"path": str(out) if out else None}

    def api_file(self, path: str) -> dict:
        """Whether a path is in the index and its kind: the app opens `lantern://preview` only for indexed files (any
        web page could ask for that URL)."""
        conn = self._conn()
        try:
            row = conn.execute("SELECT kind FROM files WHERE path = ? AND status = 'indexed'", (path,)).fetchone()
        finally:
            conn.close()
        return {"indexed": row is not None, "kind": row["kind"] if row else None}

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
        loaded = ["embeddinggemma"] if self.embedder is not None and not self.models_missing else []
        if getattr(self.query_embedder, "loaded", False):
            loaded.append("siglip_text")
        if worker is not None:
            self.perf.tick(worker.pid, progress)
        return self.perf.snapshot(progress, loaded, self.settings.index_video_frames)

    def api_diagnostics(self) -> dict:
        """A report to paste into a bug report or an AI chat (service/diagnostics.py): no file names or contents."""
        from lantern.service import diagnostics

        conn = None
        with suppress(Exception):
            conn = self._conn() if not self.index_problem else None
        try:
            return {"text": diagnostics.report(self.api_status(), conn)}
        finally:
            if conn is not None:
                conn.close()

    def api_setup(self) -> dict:
        """For the welcome window: this Mac's memory and profile, the models with what each needs, the recommended
        ones for this profile, and the suggested mode."""
        from lantern import models as models_mod

        mem = self.memory_reader()
        prof = machine.profile(mem.total)
        need = {"whisper": machine.NEEDS["whisper"], "siglip_image": machine.NEEDS["siglip_image"]}
        items = [
            {"key": m.key, "group": m.group, "label": m.label, "approx_mb": m.approx_mb,
             "group_mb": sum(o.approx_mb for o in models_mod.catalogue() if o.group == m.group),
             "downloaded": models_mod.is_downloaded(m.repo, m.revision),
             "recommended": m.group == "required" or prof == "full",
             "heavy": m.key in need, "needs_gb": round(need.get(m.key, 0) / machine.GB, 1)}
            for m in models_mod.catalogue()
        ]  # fmt: skip
        missing_mb = sum(i["approx_mb"] for i in items if i["recommended"] and not i["downloaded"])
        suggested = "on_demand" if prof == "light" else "ready"
        return {"ram_gb": round(mem.total / machine.GB), "profile": prof, "models": items,
                "suggested_mode": suggested, "recommended_mb": missing_mb}  # fmt: skip

    def api_download_models(self, models: list[str] | None = None) -> dict:
        """Start downloading models in a child process (`python -m lantern.models`) so this process stays offline."""
        targets = [str(m) for m in (models or ["required"])]
        with self.lock:
            if self.downloader is not None and self.downloader.poll() is None:
                return self._download_status() or {"running": True}
            logs_dir().mkdir(parents=True, exist_ok=True)
            log = open(logs_dir() / "models.log", "ab")  # noqa: SIM115
            env = {k: v for k, v in os.environ.items() if k not in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")}
            proc = subprocess.Popen(
                [sys.executable, "-m", "lantern.models", *targets],
                stdout=subprocess.PIPE, stderr=log, env=env, text=True, start_new_session=True,
            )  # fmt: skip
            log.close()
            self.downloader = proc
            self.download_state = {"running": True, "key": targets[0], "step": 1, "total": len(targets)}
        threading.Thread(target=self._read_downloader, args=(proc,), daemon=True).start()
        with self.lock:
            return self._download_status() or {"running": True}

    def _read_downloader(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "ModelDownload":
                with self.lock:
                    self.download_state = {"running": True, **{k: v for k, v in event.items() if k != "event"}}
        code = proc.wait()
        self.perf._sizes = None  # refresh downloaded flags and sizes in Settings → Performance
        if code != 0:
            self._warn(f"model download failed (code {code}); see logs/models.log", "download_failed", code=code)
            with self.lock:
                self.downloader = None
                self.download_state = {"running": False, "error": f"download failed (code {code}); see logs/models.log"}
            return
        from lantern.service.worker import make_query_embedder

        if self.models_missing and hasattr(self.embedder, "load"):
            try:
                self.model_thread.submit(self.embedder.load).result()
                with self.lock:
                    self.models_missing = False
                self._languages_changed()
            except Exception as exc:  # noqa: BLE001
                self._warn(f"could not load the downloaded model: {exc}", "model_load_failed", detail=exc)
        with self.lock:
            if self.query_embedder is None:
                self.query_embedder = make_query_embedder()
            self.downloader = None
            self.download_state = None
        self._enqueue_missing_transcripts()

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

    def api_restore_defaults(self) -> dict:
        """Settings back to this Mac's first-launch defaults. Kept: the folders and the parts of them left out (those
        are the user's choices of what Lantern may read, and restoring would let it read more)."""
        fresh = settings_mod.restored(self.settings)
        changed = []
        for f in fields(settings_mod.Settings):
            new, old = getattr(fresh, f.name), getattr(self.settings, f.name)
            if new == old or isinstance(new, list):
                continue
            self.api_config_set(f.name, str(new).lower() if isinstance(new, bool) else str(new))  # its side effects
            changed.append(f.name)
        with self.lock:
            lists = [k for k in ("exclude_extensions", "extra_languages")
                     if getattr(fresh, k) != getattr(self.settings, k)]  # fmt: skip
            for key in lists:
                setattr(self.settings, key, list(getattr(fresh, key)))
            settings_mod.save(self.settings)
        if "exclude_extensions" in lists:
            self._scan_all_soon()
        if "extra_languages" in lists:
            self._languages_changed()
        return {"changed": changed + lists, "settings": asdict(self.settings)}

    def api_delete_all_data(self) -> dict:
        """Everything Lantern stored: the index (files, text, vectors, places, people, music, search history), the
        settings and the logs. Downloaded models stay (they hold nothing of the user's; `lantern models` lists them).
        Lantern then starts as on the first day, with no folders."""
        self._stop_worker(wait=True)
        with self.scan_lock:
            conn = self._conn()
            roots = [r[0] for r in conn.execute("SELECT path FROM roots")]
            conn.close()
            if self.watcher:
                for root in roots:
                    self.watcher.unwatch(root)
            delete_data()
            db.connect(config.db_path()).close()  # an empty index, so the daemon keeps answering
        with self.lock:
            self.settings = settings_mod.load()  # no file: this Mac's first-launch defaults
            self.warnings, self.warning_events, self.last_run = [], [], {}
            self.approved, self.awaiting_approval, self.index_problem = False, False, None
        return {"deleted": True, "roots": len(roots)}

    def api_shutdown(self) -> dict:
        self.stopping.set()
        return {"stopping": True}

    # ---------- lifecycle ----------

    # ---------- index health ----------

    def _index_newer(self) -> bool:
        version = db.schema_version(config.db_path())
        if version is not None and version > db.SCHEMA_VERSION:
            self.index_problem = "newer"
            self._warn(f"the index is from a newer Lantern (schema {version}, this one reads {db.SCHEMA_VERSION})",
                       "index_newer")  # fmt: skip
            return True
        return False

    def _index_damaged_at_start(self) -> bool:
        """A file SQLite cannot even open. The full check (quick_check) runs in the background after the start."""
        try:
            db.schema_version(config.db_path())
        except sqlite3.DatabaseError:
            self.index_problem = "damaged"
            return True
        return False

    def _check_index_health(self) -> None:
        if config.db_path().exists() and not db.healthy(config.db_path()):
            self._stop_worker(wait=True)
            with self.lock:
                self.index_problem = "damaged"
            self._warn("the index is damaged; Lantern can rebuild it (menu bar)", "index_damaged")

    def api_rebuild_index(self) -> dict:
        """Start again with an empty index: the folders stay (read from the old file when it can still be read), the
        damaged or newer file is deleted, and everything is indexed again."""
        self._stop_worker(wait=True)
        path = config.db_path()
        roots: list[str] = []
        with suppress(sqlite3.DatabaseError):
            if path.exists():
                old = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                roots = [r[0] for r in old.execute("SELECT path FROM roots")]
                old.close()
        for suffix in ("", "-wal", "-shm"):
            with suppress(FileNotFoundError):
                Path(str(path) + suffix).unlink()
        db.connect(path).close()  # a fresh, empty index of this version
        with self.lock:
            self.index_problem = None
        for root in roots:
            with suppress(FileNotFoundError):
                self._index(root, check=False)  # the user's folders, as they were
        return {"rebuilt": True, "roots": roots}

    def _shutdown(self, path: Path) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.model_thread.shutdown(wait=False)
        with suppress(FileNotFoundError):
            path.unlink()

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
                except Exception as exc:  # report to the server client; never crash the server
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
            from lantern.embedding.mlx_backend import ModelNotDownloaded

            try:
                self.model_thread.submit(self.embedder.load).result()  # warm model: first search is fast
            except ModelNotDownloaded:
                self.models_missing = True  # the app offers a one-click download on first launch
        if self.query_embedder is None:
            from lantern.service.worker import make_query_embedder

            self.query_embedder = make_query_embedder()
        if self._index_newer() or self._index_damaged_at_start():
            # Only the API: the app explains and offers what to do (update Lantern, or rebuild the index), instead of
            # the daemon exiting and being restarted until the app gives up.
            self._serve(path)
            served = True
            while not self.stopping.is_set() and self.index_problem:
                self.stopping.wait(TICK_S)
            if self.stopping.is_set():
                self._shutdown(path)
                return 0
        else:
            served = False
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
        if not served:
            self._serve(path)
        threading.Thread(target=self._check_index_health, daemon=True).start()
        qos.set_thread_qos("utility")  # this thread now only scans and supervises; API threads keep default QoS
        if threading.current_thread() is threading.main_thread():  # launchd stops agents with SIGTERM
            signal.signal(signal.SIGTERM, lambda *_: self.stopping.set())

        try:
            while not self.stopping.is_set():
                if self.parent_pid is not None and os.getppid() != self.parent_pid:
                    self._warn("parent app exited; stopping", "parent_exited")
                    break
                if self.watcher:
                    for root in self.watcher.take_quiet(self.settings.debounce_s):
                        self.scan(root)
                self._check_power()
                self._check_memory()
                self._refresh_apps()
                self._manage_worker()
                self._refresh_meta()
                self._release_idle_query_model()
                self._release_idle_search_model()
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


def _free_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:  # unknown: never pause for that
        return MIN_FREE_BYTES

"""FSEvents watcher (watchdog). Only marks roots as dirty; the daemon rescans them after a quiet period.
The startup scan remains the source of truth (events are lost while the daemon is off)."""

import threading
import time
from pathlib import Path

from lantern.config import EXCLUDED_DIRS


class RootWatcher:
    def __init__(self):
        from watchdog.observers import Observer

        self._observer = Observer()
        self._dirty: dict[str, float] = {}
        self._watches: dict[str, object] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join(timeout=5)

    def watch(self, root: str) -> None:
        from watchdog.events import FileSystemEventHandler

        if root in self._watches or not Path(root).is_dir():
            return
        watcher = self

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                paths = [event.src_path, getattr(event, "dest_path", "") or ""]
                if all(not p or EXCLUDED_DIRS & set(Path(p).parts) for p in paths):
                    return  # .git, node_modules, build output...
                with watcher._lock:
                    watcher._dirty[root] = time.monotonic()

        self._watches[root] = self._observer.schedule(Handler(), root, recursive=True)

    def unwatch(self, root: str) -> None:
        watch = self._watches.pop(root, None)
        if watch is not None:
            self._observer.unschedule(watch)
        with self._lock:
            self._dirty.pop(root, None)

    def take_quiet(self, quiet_s: float) -> list[str]:
        """Roots with events and no new event for quiet_s seconds (removed from the dirty set)."""
        now = time.monotonic()
        with self._lock:
            ready = [r for r, t in self._dirty.items() if now - t >= quiet_s]
            for r in ready:
                del self._dirty[r]
        return ready

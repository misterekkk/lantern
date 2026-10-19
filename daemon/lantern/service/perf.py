"""Live resource monitor for the settings window: memory of the daemon and the index worker, models, progress.

Memory is the physical footprint (what Activity Monitor shows as Memory), read with proc_pid_rusage. The daemon
samples it every second on its own, so the chart already has history when the window opens; peaks come from the
kernel's lifetime maximum, so a spike between two samples is not lost.
"""

import ctypes
import os
import struct
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from lantern.extractors import kind_for

SAMPLE_S = 1.0
HISTORY = 600  # 10 minutes
RATE_WINDOW_S = 20.0  # recent rate: model loading at the start of a run must not drag it down for long
_RUSAGE_INFO_V4 = 4
_PHYS_FOOTPRINT, _LIFETIME_MAX_FOOTPRINT = 72, 240  # byte offsets in struct rusage_info_v4


def footprint(pid: int) -> tuple[float, float] | None:
    """(current, lifetime peak) physical footprint of a process in MB; None if it cannot be read."""
    try:
        lib = ctypes.CDLL("/usr/lib/libproc.dylib")
        buf = ctypes.create_string_buffer(512)
        if lib.proc_pid_rusage(pid, _RUSAGE_INFO_V4, buf) != 0:
            return None
    except OSError:
        return None
    now = struct.unpack_from("<Q", buf, _PHYS_FOOTPRINT)[0] / 2**20
    peak = struct.unpack_from("<Q", buf, _LIFETIME_MAX_FOOTPRINT)[0] / 2**20
    return now, peak


@dataclass(frozen=True)
class ModelSpec:
    key: str  # stable id for the app (localised labels)
    repo: str
    revision: str
    where: str  # daemon | worker | daemon+worker


def model_specs() -> list[ModelSpec]:
    from lantern.embedding import siglip
    from lantern.embedding.mlx_backend import REPO, REVISION
    from lantern.transcription.whisper import REPO as WHISPER_REPO
    from lantern.transcription.whisper import REVISION as WHISPER_REVISION

    return [
        ModelSpec("embeddinggemma", REPO, REVISION, "daemon+worker"),
        ModelSpec("whisper", WHISPER_REPO, WHISPER_REVISION, "worker"),
        ModelSpec("siglip_image", siglip.IMAGE_REPO, siglip.IMAGE_REVISION, "worker"),
        ModelSpec("siglip_text", siglip.TEXT_REPO, siglip.TEXT_REVISION, "daemon"),
    ]


def _snapshot_size(repo: str, revision: str) -> int | None:
    """Bytes on disk of a downloaded model snapshot (files are symlinks to blobs); None if not downloaded."""
    from huggingface_hub.constants import HF_HUB_CACHE

    snap = Path(HF_HUB_CACHE) / f"models--{repo.replace('/', '--')}" / "snapshots" / revision
    if not snap.is_dir():
        return None
    total = 0
    for dirpath, _, files in os.walk(snap):
        for name in files:
            with suppress(OSError):
                total += (Path(dirpath) / name).stat().st_size  # follows the symlink to the blob
    return total


def worker_models(progress: dict, video_frames: bool) -> list[str]:
    """Models the worker has loaded for its current file, from its progress events."""
    stage, path = progress.get("stage"), progress.get("path")
    if stage == "transcribe":
        return ["whisper"]
    if stage != "index" or not path:
        return []
    kind = kind_for(Path(path))
    if kind == "audio":
        return ["embeddinggemma:audio"]
    if kind == "video":
        return ["embeddinggemma:audio"] + (["embeddinggemma:vision", "siglip_image"] if video_frames else [])
    if kind == "image":
        return ["embeddinggemma:vision", "siglip_image"]
    return ["embeddinggemma:text"]


class PerfMonitor:
    def __init__(self) -> None:
        self.samples: deque[tuple[float, float, float | None]] = deque(maxlen=HISTORY)
        self.last_sample = 0.0
        self.worker_peak = 0.0  # lifetime peak of the running worker
        self.last_worker_peak = 0.0  # peak of the previous worker run
        self.worker_pid: int | None = None
        self.progress_marks: deque[tuple[float, int]] = deque()
        self.progress_stage: str | None = None
        self._sizes: dict[str, int | None] | None = None

    def tick(self, worker_pid: int | None, progress: dict) -> None:
        now = time.time()
        if now - self.last_sample < SAMPLE_S:
            return
        self.last_sample = now
        daemon = footprint(os.getpid())
        worker = footprint(worker_pid) if worker_pid else None
        if worker_pid != self.worker_pid:  # a run ended or started
            if self.worker_pid is not None and self.worker_peak:
                self.last_worker_peak = self.worker_peak
            self.worker_pid, self.worker_peak = worker_pid, 0.0
            self.progress_marks.clear()
        if worker:
            self.worker_peak = max(self.worker_peak, worker[1])
        self.samples.append((now, round(daemon[0], 1) if daemon else 0.0, round(worker[0], 1) if worker else None))
        stage, done = progress.get("stage"), progress.get("done")
        if stage != self.progress_stage:
            self.progress_stage = stage
            self.progress_marks.clear()
        if isinstance(done, int):
            self.progress_marks.append((now, done))
            while self.progress_marks and now - self.progress_marks[0][0] > RATE_WINDOW_S:
                self.progress_marks.popleft()

    def rate(self) -> float | None:
        """Files per second over the last RATE_WINDOW_S of the current stage."""
        if len(self.progress_marks) < 2:
            return None
        (t0, d0), (t1, d1) = self.progress_marks[0], self.progress_marks[-1]
        return round((d1 - d0) / (t1 - t0), 2) if t1 > t0 else None

    def model_sizes(self) -> dict[str, int | None]:
        if self._sizes is None:  # model folders do not change while the daemon runs
            self._sizes = {m.key: _snapshot_size(m.repo, m.revision) for m in model_specs()}
        return self._sizes

    def snapshot(self, progress: dict, daemon_loaded: list[str], video_frames: bool) -> dict:
        daemon = footprint(os.getpid())
        worker = footprint(self.worker_pid) if self.worker_pid else None
        active = worker_models(progress, video_frames) if self.worker_pid else []
        sizes = self.model_sizes()
        models = []
        for m in model_specs():
            in_worker = sorted(a.split(":")[1] if ":" in a else "" for a in active if a.split(":")[0] == m.key)
            models.append({
                "key": m.key, "repo": m.repo, "revision": m.revision, "where": m.where,
                # on disk: decimal MB like Finder and the download progress (memory stays in Activity Monitor's MB)
                "size_mb": sizes[m.key] // 10**6 if sizes.get(m.key) is not None else None,
                "downloaded": sizes.get(m.key) is not None,
                "loaded_daemon": m.key in daemon_loaded, "loaded_worker": bool(in_worker),
                "worker_parts": [p for p in in_worker if p],
            })  # fmt: skip
        now = time.time()
        return {
            "interval_s": SAMPLE_S,
            "samples": [[round(t - now, 1), d, w] for t, d, w in self.samples],
            "daemon": {"mb": round(daemon[0]) if daemon else None, "peak_mb": round(daemon[1]) if daemon else None},
            "worker": {
                "running": self.worker_pid is not None,
                "mb": round(worker[0]) if worker else None,
                "peak_mb": round(max(self.worker_peak, worker[1])) if worker else None,
                "last_peak_mb": round(self.last_worker_peak) or None,
                "stage": progress.get("stage"),
                "done": progress.get("done"),
                "total": progress.get("total"),
                "path": progress.get("path"),
                "rate": self.rate(),
            },  # fmt: skip
            "models": models,
        }

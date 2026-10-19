"""Index worker process: drains the job queue and exits.

Started by the daemon (`python -m lantern.service.worker`) with background priority. Heavy indexing models (audio and
vision towers, SigLIP image tower, Whisper) live only in this process, so their memory is returned to the system
when indexing ends and the daemon stays small. SIGTERM finishes the current file, returns the rest to the queue and
exits. Progress is reported as JSON lines on stdout.
"""

import contextlib
import json
import os
import signal
import sys

from lantern import config
from lantern.indexer import run_queue
from lantern.service import settings as settings_mod
from lantern.store import db

PRIO_DARWIN_PROCESS = 4  # <sys/resource.h>
PRIO_DARWIN_BG = 0x1000  # background policy: throttled CPU and disk I/O (`ps` shows priority 4, not 31).
# Measured: batched embeddings on the GPU are as fast as at normal priority (963 vs 980 ms for 16 passages);
# only small CPU-bound steps slow down, which is what keeps the Mac responsive while indexing.


def make_embedder():
    if os.environ.get("LANTERN_EMBEDDER") == "fake":
        from lantern.embedding.fake import FakeEmbedder

        return FakeEmbedder()
    from lantern.embedding.mlx_backend import MLXEmbedder

    return MLXEmbedder()


def make_transcriber(enabled: bool):
    if not enabled:
        return None
    if os.environ.get("LANTERN_EMBEDDER") == "fake":
        from lantern.transcription.whisper import FakeTranscriber

        return FakeTranscriber(os.environ.get("LANTERN_FAKE_TRANSCRIPT", "fake transcript"))
    from lantern.transcription import whisper

    return whisper.WhisperTranscriber() if whisper.available() else None


def make_image_embedder(settings):
    """Second photo model (SigLIP 2) when photos or frames are indexed and it is downloaded; None otherwise."""
    if not (settings.index_photos or settings.index_video_frames):
        return None
    if os.environ.get("LANTERN_EMBEDDER") == "fake":
        from lantern.embedding.fake import FakeImageEmbedder

        return FakeImageEmbedder()
    from lantern.embedding import siglip

    return siglip.SiglipImageEmbedder() if siglip.available() else None


def make_query_embedder():
    """Text side of the second photo model for searches (None when not downloaded)."""
    if os.environ.get("LANTERN_EMBEDDER") == "fake":
        from lantern.embedding.fake import FakeImageEmbedder

        return FakeImageEmbedder()
    from lantern.embedding import siglip

    return siglip.SiglipQueryEmbedder() if siglip.available() else None


def _room_for_whisper() -> bool:
    """Transcripts wait (queued) while the Mac has no room for Whisper now (service/machine.py)."""
    if os.environ.get("LANTERN_EMBEDDER") == "fake":
        return True
    from lantern.service import machine

    return machine.fits("whisper")


def emit(**event) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def main() -> int:
    stop = False

    def on_term(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    if sys.platform == "darwin" and os.environ.get("LANTERN_WORKER_PRIORITY", "background") == "background":
        with contextlib.suppress(OSError):
            os.setpriority(PRIO_DARWIN_PROCESS, 0, PRIO_DARWIN_BG)

    settings = settings_mod.load()
    if settings.index_image_text:
        from lantern.extractors import vision
        from lantern.syslang import preferred_languages

        vision.use_languages(preferred_languages(settings.extra_languages))
    conn = db.connect(config.db_path())
    progress = lambda stage, done, total, path: emit(  # noqa: E731
        event="progress", stage=stage, done=done, total=total, path=path
    )
    if "--people-only" in sys.argv:  # photos already indexed: count their people, nothing else (no queued jobs)
        from lantern import people

        n = people.refresh(conn, make_embedder(), lambda: stop, progress) if settings.people else 0
        emit(event="done", indexed=0, chunks=0, skipped=0, failed=0, transcribed=0, images=0, frames=0,
             image_texts=0, people=n, stopped=stop, errors=[])  # fmt: skip
        return 0
    stats = run_queue(
        conn,
        make_embedder(),
        progress=progress,
        transcriber=make_transcriber(settings.transcribe and _room_for_whisper()),
        should_stop=lambda: stop,
        image_embedder=make_image_embedder(settings),
        video_frames=settings.index_video_frames,
        image_text=settings.index_image_text,
        people=settings.people and settings.index_photos,
    )
    emit(
        event="done", indexed=stats.indexed, chunks=stats.chunks, skipped=stats.skipped, failed=stats.failed,
        transcribed=stats.transcribed, images=stats.images, frames=stats.frames, image_texts=stats.image_texts,
        stopped=stats.stopped,
        errors=stats.errors[:20],
    )  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())

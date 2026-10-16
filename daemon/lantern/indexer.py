"""Process queued jobs in two stages, so that heavy models are never loaded at the same time.

Stage 1, `index` jobs:
  - text/code: extract -> chunk -> embed (chunks of several files embedded together) -> one transaction per file;
  - audio/video: ffmpeg soundtrack -> 30 s windows (25 s hop) -> audio embeddings with timestamps; the audio tower
    is loaded for this stage only.
  - images: EmbeddingGemma 2 vision tower + SigLIP 2 (second photo model) -> one chunk with two vectors;
  - video files also get key frames (scene changes, near-duplicates dropped) embedded by both photo models.
Stage 2, `transcribe` jobs (audio/video, when a transcriber is given): Whisper -> ~30 s transcript chunks ->
  text embeddings + FTS. Whisper is loaded for this stage only.
A failure stays confined to its file.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from lantern.chunking import chunk_file
from lantern.chunking.transcript import group_segments
from lantern.config import AUDIO_SAMPLE_RATE, MAX_TRANSCRIBE_S
from lantern.embedding.base import Embedder, ImageEmbedder, document_title, format_document
from lantern.extractors.frames import extract_frames
from lantern.extractors.image import load_image
from lantern.extractors.media import decode_all, iter_windows
from lantern.extractors.text import SkipFile, extract_text
from lantern.queue import jobs
from lantern.queue.jobs import Job
from lantern.store import db
from lantern.transcription.whisper import Transcriber

FILES_PER_ROUND = 32
AUDIO_BATCH = 8  # windows per embed_audio call; only one batch of samples is held in memory
MEDIA_KINDS = frozenset({"audio", "video"})
IMAGE_BATCH = 8

# Bump the version of a file kind when its chunking rules or prompt formats change: indexed files of that kind
# with another version are re-indexed. text/code 2 = long-line cutting + head-only data files;
# audio/video 2 = 20 s transcript chunks; video 3 = key frames; image 1 = EmbeddingGemma (140 tokens) + SigLIP 2.
PIPELINE_VERSIONS = {"text": 2, "code": 2, "audio": 2, "video": 3, "image": 1}

Progress = Callable[[str, int, int, str], None]  # (stage, done, total, path)


@dataclass
class IndexStats:
    indexed: int = 0
    chunks: int = 0
    skipped: int = 0
    failed: int = 0
    stale: int = 0  # re-queued because of a pipeline or model change
    media: int = 0
    audio_windows: int = 0
    transcribed: int = 0
    images: int = 0
    frames: int = 0
    stopped: bool = False  # interrupted by should_stop; unprocessed jobs went back to the queue
    errors: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class _Prepared:
    job: Job
    file_id: int
    kind: str
    path: str
    rel: str
    records: list[db.ChunkRecord]
    texts: list[str]


def _file_row(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT f.id, f.path, f.kind, r.path AS root FROM files f JOIN roots r ON r.id = f.root_id WHERE f.id = ?",
        (file_id,),
    ).fetchone()


def _rel(row: sqlite3.Row) -> str:
    return Path(row["path"]).relative_to(row["root"]).as_posix()


def _prepare_text(job: Job, row: sqlite3.Row) -> _Prepared:
    path, rel = Path(row["path"]), _rel(row)
    pieces = chunk_file(path, row["kind"], extract_text(path))
    records = [
        db.ChunkRecord(
            ord=i, kind=row["kind"], text=p.text, start_line=p.start_line, end_line=p.end_line, symbol=p.symbol
        )
        for i, p in enumerate(pieces)
    ]
    texts = [format_document(document_title(rel, r.symbol), r.text) for r in records]
    return _Prepared(job, row["id"], row["kind"], row["path"], rel, records, texts)


def enqueue_stale(conn: sqlite3.Connection, model_id: int) -> int:
    """Queue indexed files whose chunks come from another pipeline version or embedding model."""
    n = 0
    for kind, version in PIPELINE_VERSIONS.items():
        rows = conn.execute(
            "SELECT id FROM files WHERE status = 'indexed' AND kind = ? "
            "AND (pipeline_version != ? OR model_id IS NOT ?)",
            (kind, version, model_id),
        ).fetchall()
        for (file_id,) in rows:
            jobs.enqueue(conn, file_id, "index")
        n += len(rows)
    return n


def run_queue(
    conn: sqlite3.Connection,
    embedder: Embedder,
    progress: Progress | None = None,
    transcriber: Transcriber | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    image_embedder: ImageEmbedder | None = None,
    video_frames: bool = True,
) -> IndexStats:
    """Process queued jobs; should_stop is checked between files (pause, battery, shutdown)."""
    stats = IndexStats()
    dim = embedder.model_info().dim
    with db.transaction(conn):
        model_id = db.ensure_model(conn, embedder.model_info())
        db.ensure_model(conn, embedder.model_info(), images=True)  # photo table of the same model
        image_model_id = db.ensure_model(conn, image_embedder.model_info(), images=True) if image_embedder else None
        jobs.recover(conn)
        stats.stale = enqueue_stale(conn, model_id)
    used_vision = False

    def failed(job: Job, file_id: int, path: str, exc: Exception, mark_file: bool = True) -> None:
        msg = f"{type(exc).__name__}: {exc}"
        stats.failed += 1
        stats.errors.append((path, msg))
        with db.transaction(conn):
            gave_up = jobs.fail(conn, job, msg)
            if mark_file:
                conn.execute(
                    "UPDATE files SET status = ?, error = ? WHERE id = ?",
                    ("error" if gave_up else "pending", msg[:1000], file_id),
                )

    def skipped(job: Job, file_id: int, reason: str) -> None:
        stats.skipped += 1
        with db.transaction(conn):
            db.delete_file_chunks(conn, file_id)
            conn.execute("UPDATE files SET status = 'skipped', error = ? WHERE id = ?", (reason, file_id))
            jobs.complete(conn, job)

    def write(
        item: _Prepared, vectors: np.ndarray, kinds: set[str] | None = None, then_transcribe=False,
        extra: list[tuple[int, np.ndarray]] = (), complete: bool = True,
    ) -> None:  # fmt: skip
        with db.transaction(conn):
            db.replace_file_chunks(
                conn, item.file_id, item.rel, item.records, vectors, model_id, PIPELINE_VERSIONS[item.kind], kinds,
                extra,
            )  # fmt: skip
            if then_transcribe:
                jobs.enqueue(conn, item.file_id, "transcribe")
            if complete:
                jobs.complete(conn, item.job)
        stats.chunks += len(item.records)

    def embed_visual(images: list) -> tuple[np.ndarray, list[tuple[int, np.ndarray]]]:
        """Both photo models: EmbeddingGemma vectors + (SigLIP model id, vectors) when the second model is present."""
        nonlocal used_vision
        used_vision = True
        if not images:
            return np.zeros((0, dim), dtype=np.float32), []
        vecs = np.concatenate(
            [embedder.embed_images(images[i : i + IMAGE_BATCH]) for i in range(0, len(images), IMAGE_BATCH)]
        )
        extra = [(image_model_id, image_embedder.embed_images(images))] if image_embedder else []
        return vecs, extra

    def index_image(job: Job, row: sqlite3.Row) -> None:
        image = load_image(Path(row["path"]))
        vecs, extra = embed_visual([image])
        record = db.ChunkRecord(ord=0, kind="image", text=None)
        write(_Prepared(job, row["id"], "image", row["path"], _rel(row), [record], []), vecs, extra=extra)
        stats.images += 1
        stats.indexed += 1

    def release(unprocessed: list[Job]) -> None:
        stats.stopped = True
        with db.transaction(conn):
            for job in unprocessed:
                conn.execute("UPDATE jobs SET status = 'queued' WHERE id = ? AND status = 'running'", (job.id,))

    def index_media(job: Job, row: sqlite3.Row) -> None:
        path, rel = Path(row["path"]), _rel(row)
        records, vectors, batch, starts = [], [], [], []

        def flush() -> None:
            if batch:
                vectors.append(embedder.embed_audio(batch))
                batch.clear()

        has_audio = True
        try:
            for start_s, samples in iter_windows(path):
                starts.append((start_s, len(samples)))
                batch.append(samples)
                if len(batch) == AUDIO_BATCH:
                    flush()
            flush()
        except SkipFile:
            if row["kind"] != "video":
                raise  # an audio file without audio is skipped
            has_audio = False  # silent video: frames only
        frames = []
        if row["kind"] == "video" and video_frames:
            try:
                frames = extract_frames(path)
            except SkipFile:
                frames = []
        if not has_audio and not frames:
            raise SkipFile("no audio track and no video frames")

        for i, (start_s, n) in enumerate(starts):
            end_ms = int((start_s + n / AUDIO_SAMPLE_RATE) * 1000)
            records.append(db.ChunkRecord(ord=i, kind="audio", text=None, start_ms=int(start_s * 1000), end_ms=end_ms))
        vecs = np.concatenate(vectors) if vectors else np.zeros((0, dim), dtype=np.float32)
        item = _Prepared(job, row["id"], row["kind"], row["path"], rel, records, [])
        write(item, vecs, kinds={"audio"}, then_transcribe=transcriber is not None and has_audio, complete=False)

        frame_records = [
            db.ChunkRecord(ord=i, kind="frame", text=None, start_ms=int(f.start_s * 1000), end_ms=int(f.end_s * 1000))
            for i, f in enumerate(frames)
        ]
        fvecs, extra = embed_visual([f.image for f in frames])
        write(_Prepared(job, row["id"], row["kind"], row["path"], rel, frame_records, []), fvecs, kinds={"frame"},
              extra=extra)  # fmt: skip
        stats.media += 1
        stats.indexed += 1
        stats.audio_windows += len(records)
        stats.frames += len(frame_records)

    # ---- stage 1: index ----
    total = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'queued' AND kind = 'index'").fetchone()[0]
    done = 0
    while True:
        with db.transaction(conn):
            claimed = jobs.claim(conn, "index", FILES_PER_ROUND)
        if not claimed:
            break

        prepared: list[_Prepared] = []
        for n, job in enumerate(claimed):
            if should_stop():
                release(claimed[n:])  # text files already prepared in this round are still embedded below
                break
            row = _file_row(conn, job.file_id)
            done += 1
            if row is None:  # file deleted meanwhile; cascade removed the job already
                continue
            if progress:
                progress("index", done, total, row["path"])
            try:
                if row["kind"] in MEDIA_KINDS:
                    index_media(job, row)
                elif row["kind"] == "image":
                    index_image(job, row)
                else:
                    prepared.append(_prepare_text(job, row))
            except SkipFile as exc:
                skipped(job, row["id"], str(exc))
            except Exception as exc:  # one bad file must not stop the queue
                failed(job, row["id"], row["path"], exc)

        all_texts = [t for item in prepared for t in item.texts]
        try:
            vectors = embedder.embed_text(all_texts) if all_texts else np.zeros((0, dim), dtype=np.float32)
        except Exception:
            # Isolate the culprit: embed file by file.
            for item in prepared:
                try:
                    vecs = embedder.embed_text(item.texts) if item.texts else np.zeros((0, dim), dtype=np.float32)
                    write(item, vecs)
                    stats.indexed += 1
                except Exception as exc:
                    failed(item.job, item.file_id, item.path, exc)
            continue

        offset = 0
        for item in prepared:
            n = len(item.texts)
            try:
                write(item, vectors[offset : offset + n])
                stats.indexed += 1
            except Exception as exc:
                failed(item.job, item.file_id, item.path, exc)
            offset += n
        if stats.stopped:
            break

    if stats.media:
        embedder.unload_audio()
    if used_vision:
        embedder.unload_vision()
        if image_embedder:
            image_embedder.unload()

    # ---- stage 2: transcribe ----
    if transcriber is None or stats.stopped:
        return stats
    total = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'queued' AND kind = 'transcribe'").fetchone()[0]
    done = 0
    try:
        while True:
            with db.transaction(conn):
                claimed = jobs.claim(conn, "transcribe", FILES_PER_ROUND)
            if not claimed:
                break
            for n, job in enumerate(claimed):
                if should_stop():
                    release(claimed[n:])
                    break
                row = _file_row(conn, job.file_id)
                done += 1
                if row is None:
                    continue
                if progress:
                    progress("transcribe", done, total, row["path"])
                try:
                    samples = decode_all(Path(row["path"]), MAX_TRANSCRIBE_S)
                    pieces = group_segments(transcriber.transcribe(samples))
                    rel = _rel(row)
                    records = [
                        db.ChunkRecord(ord=i, kind="transcript", text=p.text, start_ms=p.start_ms, end_ms=p.end_ms)
                        for i, p in enumerate(pieces)
                    ]
                    texts = [format_document(rel, r.text) for r in records]
                    vecs = embedder.embed_text(texts) if texts else np.zeros((0, dim), dtype=np.float32)
                    item = _Prepared(job, row["id"], row["kind"], row["path"], rel, records, texts)
                    write(item, vecs, kinds={"transcript"})
                    stats.transcribed += 1
                except SkipFile as exc:
                    with db.transaction(conn):
                        jobs.complete(conn, job)
                    stats.errors.append((row["path"], f"transcription skipped: {exc}"))
                except Exception as exc:
                    failed(job, row["id"], row["path"], exc, mark_file=False)
            if stats.stopped:
                break
    finally:
        transcriber.unload()
    return stats

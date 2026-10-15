"""Process queued index jobs: extract -> chunk -> embed -> write.

Chunks from several files are embedded together (small files would otherwise give the GPU batches of 1-3 texts);
each file is still written in its own transaction, and a failure stays confined to its file.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from lantern.chunking import chunk_file
from lantern.embedding.base import Embedder, document_title, format_document
from lantern.extractors.text import SkipFile, extract_text
from lantern.queue import jobs
from lantern.queue.jobs import Job
from lantern.store import db

FILES_PER_ROUND = 32
# Bump when chunking rules or prompt formats change: indexed files with another version get re-indexed.
# 1 = Phase 2 initial, 2 = long-line cutting + head-only data files.
PIPELINE_VERSION = 2


@dataclass
class IndexStats:
    indexed: int = 0
    chunks: int = 0
    skipped: int = 0
    failed: int = 0
    stale: int = 0  # re-queued because of a pipeline or model change
    errors: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class _Prepared:
    job: Job
    file_id: int
    path: str
    rel: str
    records: list[db.ChunkRecord]
    texts: list[str]


def _file_row(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT f.id, f.path, f.kind, r.path AS root FROM files f JOIN roots r ON r.id = f.root_id WHERE f.id = ?",
        (file_id,),
    ).fetchone()


def _prepare(job: Job, row: sqlite3.Row) -> _Prepared:
    path = Path(row["path"])
    rel = path.relative_to(row["root"]).as_posix()
    pieces = chunk_file(path, row["kind"], extract_text(path))
    records = [
        db.ChunkRecord(
            ord=i, kind=row["kind"], text=p.text, start_line=p.start_line, end_line=p.end_line, symbol=p.symbol
        )
        for i, p in enumerate(pieces)
    ]
    texts = [format_document(document_title(rel, r.symbol), r.text) for r in records]
    return _Prepared(job, row["id"], row["path"], rel, records, texts)


def enqueue_stale(conn: sqlite3.Connection, model_id: int) -> int:
    """Queue indexed files whose chunks come from another pipeline version or embedding model."""
    rows = conn.execute(
        "SELECT id FROM files WHERE status = 'indexed' AND (pipeline_version != ? OR model_id IS NOT ?)",
        (PIPELINE_VERSION, model_id),
    ).fetchall()
    for (file_id,) in rows:
        jobs.enqueue(conn, file_id, "index")
    return len(rows)


def run_queue(
    conn: sqlite3.Connection,
    embedder: Embedder,
    progress: Callable[[int, int, str], None] | None = None,
) -> IndexStats:
    stats = IndexStats()
    dim = embedder.model_info().dim
    with db.transaction(conn):
        model_id = db.ensure_model(conn, embedder.model_info())
        jobs.recover(conn)
        stats.stale = enqueue_stale(conn, model_id)
    total = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'queued' AND kind = 'index'").fetchone()[0]
    done = 0

    def failed(job: Job, file_id: int, path: str, exc: Exception) -> None:
        msg = f"{type(exc).__name__}: {exc}"
        stats.failed += 1
        stats.errors.append((path, msg))
        with db.transaction(conn):
            gave_up = jobs.fail(conn, job, msg)
            conn.execute(
                "UPDATE files SET status = ?, error = ? WHERE id = ?",
                ("error" if gave_up else "pending", msg[:1000], file_id),
            )

    def write(item: _Prepared, vectors: np.ndarray) -> None:
        with db.transaction(conn):
            db.replace_file_chunks(conn, item.file_id, item.rel, item.records, vectors, model_id, PIPELINE_VERSION)
            jobs.complete(conn, item.job)
        stats.indexed += 1
        stats.chunks += len(item.records)

    while True:
        with db.transaction(conn):
            claimed = jobs.claim(conn, "index", FILES_PER_ROUND)
        if not claimed:
            break

        prepared: list[_Prepared] = []
        for job in claimed:
            row = _file_row(conn, job.file_id)
            done += 1
            if row is None:  # file deleted meanwhile; cascade removed the job already
                continue
            if progress:
                progress(done, total, row["path"])
            try:
                prepared.append(_prepare(job, row))
            except SkipFile as exc:
                stats.skipped += 1
                with db.transaction(conn):
                    db.delete_file_chunks(conn, row["id"])
                    conn.execute("UPDATE files SET status = 'skipped', error = ? WHERE id = ?", (str(exc), row["id"]))
                    jobs.complete(conn, job)
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
                except Exception as exc:
                    failed(item.job, item.file_id, item.path, exc)
            continue

        offset = 0
        for item in prepared:
            n = len(item.texts)
            try:
                write(item, vectors[offset : offset + n])
            except Exception as exc:
                failed(item.job, item.file_id, item.path, exc)
            offset += n
    return stats

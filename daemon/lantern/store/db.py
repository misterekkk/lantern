"""SQLite access: connection setup, schema, per-model vector tables, chunk writes and deletes."""

import sqlite3
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sqlite_vec

from lantern.textnorm import fold

SCHEMA_VERSION = 1
_SCHEMA = Path(__file__).with_name("schema.sql")


@dataclass(frozen=True)
class ModelInfo:
    name: str
    revision: str
    backend: str
    dtype: str
    dim: int


@dataclass(frozen=True)
class ChunkRecord:
    ord: int
    kind: str
    text: str
    start_line: int | None = None
    end_line: int | None = None
    symbol: str | None = None


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)  # explicit transactions via transaction()
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA secure_delete = ON")  # overwrite deleted content instead of leaving it in free pages
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        conn.executescript(_SCHEMA.read_text())
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif version != SCHEMA_VERSION:
        raise RuntimeError(f"index schema version {version} is not supported (expected {SCHEMA_VERSION})")
    return conn


class transaction:  # noqa: N801 - used as a context manager
    """BEGIN IMMEDIATE ... COMMIT/ROLLBACK on a connection in autocommit mode."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self) -> sqlite3.Connection:
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")


def now() -> int:
    return int(time.time())


def vec_table(model_id: int) -> str:
    return f"vec_chunks_{int(model_id)}"


def find_model(conn: sqlite3.Connection, info: ModelInfo) -> int | None:
    row = conn.execute(
        "SELECT id FROM embedding_models WHERE name=? AND revision=? AND backend=? AND dtype=? AND dim=?",
        (info.name, info.revision, info.backend, info.dtype, info.dim),
    ).fetchone()
    return row[0] if row else None


def ensure_model(conn: sqlite3.Connection, info: ModelInfo) -> int:
    """Register an embedding model and create its vector table. Returns embedding_models.id."""
    conn.execute(
        "INSERT OR IGNORE INTO embedding_models(name, revision, backend, dtype, dim) VALUES (?, ?, ?, ?, ?)",
        (info.name, info.revision, info.backend, info.dtype, info.dim),
    )
    model_id = find_model(conn, info)
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {vec_table(model_id)} USING vec0("
        f"chunk_id INTEGER PRIMARY KEY, embedding FLOAT[{info.dim}] distance_metric=cosine)"
    )
    return model_id


def all_model_ids(conn: sqlite3.Connection) -> list[int]:
    return [r[0] for r in conn.execute("SELECT id FROM embedding_models")]


def ensure_root(conn: sqlite3.Connection, path: Path) -> int:
    conn.execute("INSERT OR IGNORE INTO roots(path) VALUES (?)", (str(path),))
    return conn.execute("SELECT id FROM roots WHERE path = ?", (str(path),)).fetchone()[0]


def _delete_chunk_rows(conn: sqlite3.Connection, chunk_ids: Sequence[int]) -> None:
    if not chunk_ids:
        return
    for start in range(0, len(chunk_ids), 500):
        batch = list(chunk_ids[start : start + 500])
        marks = ",".join("?" * len(batch))
        conn.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({marks})", batch)
        for model_id in all_model_ids(conn):
            conn.execute(f"DELETE FROM {vec_table(model_id)} WHERE chunk_id IN ({marks})", batch)
        conn.execute(f"DELETE FROM chunks WHERE id IN ({marks})", batch)


def delete_file_chunks(conn: sqlite3.Connection, file_id: int) -> None:
    """Remove all chunks of a file with their FTS rows and vectors (call inside a transaction)."""
    ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE file_id = ?", (file_id,))]
    _delete_chunk_rows(conn, ids)


def delete_files(conn: sqlite3.Connection, file_ids: Iterable[int]) -> int:
    """Remove files and everything derived from them (call inside a transaction)."""
    n = 0
    for file_id in file_ids:
        delete_file_chunks(conn, file_id)
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))  # cascades to jobs
        n += 1
    return n


def replace_file_chunks(
    conn: sqlite3.Connection,
    file_id: int,
    rel_path: str,
    chunks: Sequence[ChunkRecord],
    vectors: np.ndarray,
    model_id: int,
) -> None:
    """Idempotent re-index of one file: drop old chunks, insert new chunks, FTS rows and vectors."""
    if len(chunks) != len(vectors):
        raise ValueError("chunks and vectors length mismatch")
    delete_file_chunks(conn, file_id)
    table = vec_table(model_id)
    for chunk, vec in zip(chunks, vectors, strict=True):
        cur = conn.execute(
            "INSERT INTO chunks(file_id, ord, kind, start_line, end_line, symbol, text) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (file_id, chunk.ord, chunk.kind, chunk.start_line, chunk.end_line, chunk.symbol, chunk.text),
        )
        chunk_id = cur.lastrowid
        conn.execute(
            "INSERT INTO chunks_fts(rowid, text, path, symbol) VALUES (?, ?, ?, ?)",
            (chunk_id, fold(chunk.text), fold(rel_path), fold(chunk.symbol or "")),
        )
        conn.execute(
            f"INSERT INTO {table}(chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, np.asarray(vec, dtype=np.float32).tobytes()),
        )
    conn.execute(
        "UPDATE files SET status = 'indexed', error = NULL, indexed_at = ? WHERE id = ?",
        (now(), file_id),
    )


def update_file_path(conn: sqlite3.Connection, file_id: int, new_path: str, new_rel_path: str) -> None:
    """Move: change the path and the FTS path column, keep chunks and vectors."""
    conn.execute("UPDATE files SET path = ? WHERE id = ?", (new_path, file_id))
    conn.execute(
        "UPDATE chunks_fts SET path = ? WHERE rowid IN (SELECT id FROM chunks WHERE file_id = ?)",
        (fold(new_rel_path), file_id),
    )

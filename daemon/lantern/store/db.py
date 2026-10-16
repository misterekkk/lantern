"""SQLite access: connection setup, schema, per-model vector tables, chunk writes and deletes."""

import sqlite3
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sqlite_vec

from lantern.textnorm import fold, is_blob

SCHEMA_VERSION = 11
_SCHEMA = Path(__file__).with_name("schema.sql")


def _refold_fts(conn: sqlite3.Connection) -> None:
    """Re-fold stored FTS values with the wider fold table (it extends the old ł -> l, so folding again is enough):
    keyword search works for ø, ß, æ ... without re-embedding anything."""
    conn.create_function("lantern_fold", 1, fold, deterministic=True)
    conn.execute(
        "UPDATE chunks_fts SET text = lantern_fold(text), path = lantern_fold(path), symbol = lantern_fold(symbol) "
        "WHERE lantern_fold(text) != text OR lantern_fold(path) != path OR lantern_fold(symbol) != symbol"
    )


def _drop_blob_chunks(conn: sqlite3.Connection) -> None:
    """Remove text/code chunks that are encoded data (hex/base64 images in SQL dumps, CSS, HTML) with their FTS rows
    and vectors; new ones are no longer made (indexer._prepare_text)."""
    rows = conn.execute("SELECT id, text FROM chunks WHERE kind IN ('text', 'code') AND length(text) >= 64")
    _delete_chunk_rows(conn, [r[0] for r in rows if is_blob(r[1])])


def _requeue_trimmed_files(conn: sqlite3.Connection) -> None:
    """Version 9 also dropped some link-heavy HTML passages (long link paths looked like base64). Files that lost
    chunks (gaps in their chunk numbers, or none left) are indexed again with the narrower rule."""
    conn.execute(
        "UPDATE files SET pipeline_version = 0 WHERE kind IN ('text', 'code') AND status = 'indexed' AND (id IN ("
        "SELECT file_id FROM chunks WHERE kind IN ('text', 'code') GROUP BY file_id HAVING max(ord) + 1 != count(*)"
        ") OR NOT EXISTS (SELECT 1 FROM chunks c WHERE c.file_id = files.id))"
    )


# Upgrades from version N to N + 1. A fresh database gets schema.sql directly.
_MIGRATIONS: dict[int, list[str | Callable[[sqlite3.Connection], None]]] = {
    1: [
        "ALTER TABLE files ADD COLUMN pipeline_version INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE files ADD COLUMN model_id INTEGER",
        "INSERT INTO chunks_fts(chunks_fts, rank) VALUES ('secure-delete', 1)",
    ],
    2: [
        "CREATE TABLE IF NOT EXISTS symbols (id INTEGER PRIMARY KEY, "
        "file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE, name TEXT NOT NULL, name_key TEXT NOT NULL, "
        "kind TEXT NOT NULL, parent TEXT, signature TEXT NOT NULL, line INTEGER NOT NULL, end_line INTEGER NOT NULL)",
        "CREATE INDEX IF NOT EXISTS symbols_key ON symbols(name_key)",
        "CREATE INDEX IF NOT EXISTS symbols_file ON symbols(file_id)",
    ],
    3: ["ALTER TABLE chunks ADD COLUMN page INTEGER"],
    4: [_refold_fts],
    5: [
        """CREATE TABLE IF NOT EXISTS apps (
    path       TEXT PRIMARY KEY,
    name       TEXT NOT NULL,      -- as Finder shows it
    names      TEXT NOT NULL,      -- JSON list: bundle names and translations in the preferred languages
    keywords   TEXT NOT NULL,      -- JSON list (MDItemKeywords)
    category   TEXT,               -- App Store category, e.g. social-networking
    bundle_id  TEXT,
    mtime      REAL NOT NULL,
    languages  TEXT NOT NULL,      -- preferred languages the names were read for
    document   TEXT NOT NULL,      -- the text the vector was made of
    model_id   INTEGER NOT NULL REFERENCES embedding_models(id),
    embedding  BLOB NOT NULL       -- float32, only compared with query vectors of the same model_id
)"""
    ],
    6: [
        """CREATE TABLE IF NOT EXISTS system_settings (
    key        TEXT PRIMARY KEY,   -- pane bundle id, or bundle id/setting key
    pane       TEXT NOT NULL,      -- pane name in the first preferred language
    title      TEXT NOT NULL,      -- the pane name for a pane, the setting's title otherwise
    names      TEXT NOT NULL,      -- JSON list: the same in the other preferred languages
    keywords   TEXT NOT NULL,      -- JSON list: Apple's index words for the setting
    url        TEXT NOT NULL,      -- x-apple.systempreferences:<pane>
    symbol     TEXT,               -- SF Symbol of the pane icon
    color      TEXT,               -- its enclosure colour
    languages  TEXT NOT NULL,
    document   TEXT NOT NULL,      -- the text the vector was made of
    model_id   INTEGER NOT NULL REFERENCES embedding_models(id),
    embedding  BLOB NOT NULL       -- float32, compared only with query vectors of the same model_id
)"""
    ],
    7: [
        """CREATE TABLE IF NOT EXISTS shortcuts (
    id    TEXT PRIMARY KEY,   -- Shortcuts' identifier
    name  TEXT NOT NULL
)"""
    ],
    8: [_drop_blob_chunks],
    9: [_requeue_trimmed_files],
    10: [
        """CREATE TABLE IF NOT EXISTS music (
    id         TEXT PRIMARY KEY,   -- Music's persistent ID (16 hex digits)
    kind       TEXT NOT NULL,      -- song | playlist
    title      TEXT NOT NULL,      -- song title or playlist name
    artist     TEXT NOT NULL DEFAULT '',
    album      TEXT NOT NULL DEFAULT '',
    track      INTEGER NOT NULL DEFAULT 0,
    downloaded INTEGER NOT NULL DEFAULT 0,
    members    TEXT NOT NULL DEFAULT '[]',  -- JSON: a song's playlist ids, a playlist's song ids (in order)
    document   TEXT NOT NULL,      -- the text the vector was made of
    model_id   INTEGER NOT NULL REFERENCES embedding_models(id),
    embedding  BLOB NOT NULL       -- float32, only compared with query vectors of the same model_id
)"""
    ],
}

# Auto-compaction threshold: free pages as a share of the file.
COMPACT_FREE_RATIO = 0.25
COMPACT_MIN_PAGES = 1000


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
    kind: str  # text | code | pdf | audio | transcript | image | frame | ocr (text in an image) | screen (on a frame)
    text: str | None  # None for audio windows and photos (vector only)
    start_line: int | None = None
    end_line: int | None = None
    symbol: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    page: int | None = None  # PDF page


class NewerSchemaError(RuntimeError):
    """The index was upgraded by a newer Lantern (e.g. an index worker started from updated code)."""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)  # explicit transactions via transaction()
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA busy_timeout = 10000")  # daemon and index worker write concurrently (WAL)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA secure_delete = ON")  # overwrite deleted content instead of leaving it in free pages
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        conn.executescript(_SCHEMA.read_text())
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif version > SCHEMA_VERSION:
        raise NewerSchemaError(f"index schema version {version} is newer than this Lantern ({SCHEMA_VERSION})")
    else:
        with transaction(conn):
            while version < SCHEMA_VERSION:
                for statement in _MIGRATIONS[version]:
                    statement(conn) if callable(statement) else conn.execute(statement)
                version += 1
                conn.execute(f"PRAGMA user_version = {version}")
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


VISUAL_KINDS = frozenset({"image", "frame"})  # chunks whose vectors live in the photo tables


def vec_table(model_id: int, images: bool = False) -> str:
    """Vector table of a model: text/code/audio chunks, or photos and video frames (kept apart: text-to-image
    similarities are on another scale and are fused separately, see search/hybrid.py)."""
    return f"vec_{'images' if images else 'chunks'}_{int(model_id)}"


def find_model(conn: sqlite3.Connection, info: ModelInfo) -> int | None:
    row = conn.execute(
        "SELECT id FROM embedding_models WHERE name=? AND revision=? AND backend=? AND dtype=? AND dim=?",
        (info.name, info.revision, info.backend, info.dtype, info.dim),
    ).fetchone()
    return row[0] if row else None


def ensure_model(conn: sqlite3.Connection, info: ModelInfo, images: bool = False) -> int:
    """Register an embedding model and create its vector table (text or photo). Returns embedding_models.id."""
    conn.execute(
        "INSERT OR IGNORE INTO embedding_models(name, revision, backend, dtype, dim) VALUES (?, ?, ?, ?, ?)",
        (info.name, info.revision, info.backend, info.dtype, info.dim),
    )
    model_id = find_model(conn, info)
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {vec_table(model_id, images)} USING vec0("
        f"chunk_id INTEGER PRIMARY KEY, embedding FLOAT[{info.dim}] distance_metric=cosine)"
    )
    return model_id


def all_model_ids(conn: sqlite3.Connection) -> list[int]:
    return [r[0] for r in conn.execute("SELECT id FROM embedding_models")]


def vector_tables(conn: sqlite3.Connection) -> list[str]:
    """All vec0 tables (text and photo tables of every model)."""
    return [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND sql LIKE 'CREATE VIRTUAL TABLE%USING vec0%'"
        )
    ]


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
        for table in vector_tables(conn):
            conn.execute(f"DELETE FROM {table} WHERE chunk_id IN ({marks})", batch)
        conn.execute(f"DELETE FROM chunks WHERE id IN ({marks})", batch)


def delete_file_chunks(conn: sqlite3.Connection, file_id: int, kinds: Iterable[str] | None = None) -> None:
    """Remove a file's chunks (optionally only some kinds) with their FTS rows and vectors (inside a transaction)."""
    if kinds is None:
        conn.execute("DELETE FROM symbols WHERE file_id = ?", (file_id,))
        rows = conn.execute("SELECT id FROM chunks WHERE file_id = ?", (file_id,))
    else:
        kinds = list(kinds)
        marks = ",".join("?" * len(kinds))
        rows = conn.execute(f"SELECT id FROM chunks WHERE file_id = ? AND kind IN ({marks})", (file_id, *kinds))
    _delete_chunk_rows(conn, [r[0] for r in rows])


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
    pipeline_version: int,
    kinds: Iterable[str] | None = None,
    extra: Sequence[tuple[int, np.ndarray]] = (),
) -> None:
    """Idempotent re-index of one file: drop its old chunks (only `kinds` if given), insert new chunks, FTS rows
    and vectors. Audio windows and photos (text None) get an FTS row with an empty text, so the file name stays
    searchable. Photos/frames go to the photo tables; `extra` adds vectors of a second photo model
    as (model_id, vectors)."""
    if len(chunks) != len(vectors) or any(len(v) != len(chunks) for _, v in extra):
        raise ValueError("chunks and vectors length mismatch")
    delete_file_chunks(conn, file_id, kinds)
    visual = bool(chunks) and all(c.kind in VISUAL_KINDS for c in chunks)
    table = vec_table(model_id, images=visual)
    extra_tables = [(vec_table(mid, images=True), vecs) for mid, vecs in extra]
    for i, (chunk, vec) in enumerate(zip(chunks, vectors, strict=True)):
        cur = conn.execute(
            "INSERT INTO chunks(file_id, ord, kind, start_line, end_line, start_ms, end_ms, symbol, text, page) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                file_id,
                chunk.ord,
                chunk.kind,
                chunk.start_line,
                chunk.end_line,
                chunk.start_ms,
                chunk.end_ms,
                chunk.symbol,
                chunk.text,
                chunk.page,
            ),  # fmt: skip
        )
        chunk_id = cur.lastrowid
        conn.execute(
            "INSERT INTO chunks_fts(rowid, text, path, symbol) VALUES (?, ?, ?, ?)",
            (chunk_id, fold(chunk.text or ""), fold(rel_path), fold(chunk.symbol or "")),
        )
        conn.execute(
            f"INSERT INTO {table}(chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, np.asarray(vec, dtype=np.float32).tobytes()),
        )
        for extra_table, vecs in extra_tables:
            conn.execute(
                f"INSERT INTO {extra_table}(chunk_id, embedding) VALUES (?, ?)",
                (chunk_id, np.asarray(vecs[i], dtype=np.float32).tobytes()),
            )
    conn.execute(
        "UPDATE files SET status = 'indexed', error = NULL, indexed_at = ?, pipeline_version = ?, model_id = ? "
        "WHERE id = ?",
        (now(), pipeline_version, model_id, file_id),
    )


def replace_file_symbols(conn: sqlite3.Connection, file_id: int, symbols: Sequence) -> None:
    """Replace a code file's symbols (chunking.symbols.Symbol) inside the caller's transaction."""
    from lantern.chunking.symbols import name_key

    conn.execute("DELETE FROM symbols WHERE file_id = ?", (file_id,))
    conn.executemany(
        "INSERT INTO symbols(file_id, name, name_key, kind, parent, signature, line, end_line) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(file_id, s.name, name_key(s.name), s.kind, s.parent, s.signature, s.line, s.end_line) for s in symbols],
    )


def update_file_path(conn: sqlite3.Connection, file_id: int, new_path: str, new_rel_path: str) -> None:
    """Move: change the path and the FTS path column, keep chunks and vectors."""
    conn.execute("UPDATE files SET path = ? WHERE id = ?", (new_path, file_id))
    conn.execute(
        "UPDATE chunks_fts SET path = ? WHERE rowid IN (SELECT id FROM chunks WHERE file_id = ?)",
        (fold(new_rel_path), file_id),
    )


def remove_root(conn: sqlite3.Connection, root_id: int) -> int:
    """Drop a root folder and everything derived from its files (call inside a transaction)."""
    ids = [r[0] for r in conn.execute("SELECT id FROM files WHERE root_id = ?", (root_id,))]
    delete_files(conn, ids)
    conn.execute("DELETE FROM roots WHERE id = ?", (root_id,))
    return len(ids)


def free_ratio(conn: sqlite3.Connection) -> tuple[float, int]:
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    return (free / pages if pages else 0.0), pages


def compact(conn: sqlite3.Connection) -> None:
    """Make deleted data physically disappear: merge FTS segments, rebuild the file, truncate the WAL."""
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('optimize')")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("VACUUM")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def maybe_compact(conn: sqlite3.Connection) -> bool:
    ratio, pages = free_ratio(conn)
    if pages >= COMPACT_MIN_PAGES and ratio >= COMPACT_FREE_RATIO:
        compact(conn)
        return True
    return False

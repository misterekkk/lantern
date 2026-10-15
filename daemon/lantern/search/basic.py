"""Candidate lists for hybrid search: vector KNN and FTS5/BM25, each ranked by file (best chunk per file)."""

import sqlite3
from dataclasses import dataclass

import numpy as np

from lantern.embedding.base import Embedder, format_query
from lantern.store import db
from lantern.textnorm import fts_query

# Column weights for bm25(): text, path, symbol. A hit in a function name or path says more than one in a body.
BM25_WEIGHTS = "1.0, 1.5, 3.0"


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    path: str
    start_line: int | None
    end_line: int | None
    symbol: str | None
    score: float  # cosine similarity (vector) or -bm25 (keyword); higher is better
    preview: str
    kind: str = "text"
    start_ms: int | None = None  # audio windows and transcript chunks
    end_ms: int | None = None


def _hits(conn: sqlite3.Connection, scored: list[tuple[int, float]]) -> list[Hit]:
    if not scored:
        return []
    marks = ",".join("?" * len(scored))
    rows = {
        r["id"]: r
        for r in conn.execute(
            f"SELECT c.id, c.start_line, c.end_line, c.symbol, c.text, c.kind, c.start_ms, c.end_ms, f.path "
            f"FROM chunks c "
            f"JOIN files f ON f.id = c.file_id WHERE c.id IN ({marks})",
            [cid for cid, _ in scored],
        )
    }
    out = []
    for cid, score in scored:
        r = rows.get(cid)
        if r is None:
            continue
        text = r["text"] if r["text"] is not None else _transcript_at(conn, cid, r["start_ms"], r["end_ms"])
        preview = " ".join((text or "").split())[:160]
        out.append(
            Hit(
                cid,
                r["path"],
                r["start_line"],
                r["end_line"],
                r["symbol"],
                score,
                preview,
                r["kind"],
                r["start_ms"],
                r["end_ms"],
            )  # fmt: skip
        )
    return out


def _transcript_at(conn: sqlite3.Connection, chunk_id: int, start_ms: int | None, end_ms: int | None) -> str:
    """Preview for an audio-window match: the transcript text spoken in the same time range, if any."""
    if start_ms is None:
        return ""
    rows = conn.execute(
        "SELECT t.text FROM chunks a JOIN chunks t ON t.file_id = a.file_id AND t.kind = 'transcript' "
        "WHERE a.id = ? AND t.end_ms > ? AND t.start_ms < ? ORDER BY t.start_ms",
        (chunk_id, start_ms, end_ms),
    ).fetchall()
    return " ".join(r[0] for r in rows)


def _best_per_file(hits: list[Hit], limit: int) -> list[Hit]:
    seen, out = set(), []
    for h in hits:
        if h.path in seen:
            continue
        seen.add(h.path)
        out.append(h)
        if len(out) == limit:
            break
    return out


def vector_search(conn: sqlite3.Connection, embedder: Embedder, query: str, limit: int = 10) -> list[Hit]:
    model_id = db.find_model(conn, embedder.model_info())
    if model_id is None:  # nothing indexed with this model yet
        return []
    qv = embedder.embed_text([format_query(query)])[0].astype(np.float32)
    rows = conn.execute(
        f"SELECT chunk_id, distance FROM {db.vec_table(model_id)} WHERE embedding MATCH ? AND k = ?",
        (qv.tobytes(), limit * 5),
    ).fetchall()
    return _best_per_file(_hits(conn, [(r[0], 1.0 - r[1]) for r in rows]), limit)


def keyword_search(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[Hit]:
    expr = fts_query(query)
    if expr is None:
        return []
    rows = conn.execute(
        f"SELECT rowid, bm25(chunks_fts, {BM25_WEIGHTS}) AS s FROM chunks_fts "
        "WHERE chunks_fts MATCH ? ORDER BY s LIMIT ?",
        (expr, limit * 5),
    ).fetchall()
    return _best_per_file(_hits(conn, [(r[0], -r[1]) for r in rows]), limit)

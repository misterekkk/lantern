"""Candidate lists for hybrid search: vector KNN and FTS5/BM25, each ranked by file (best chunk per file)."""

import re
import sqlite3
from dataclasses import dataclass, field, replace

import numpy as np

from lantern.embedding.base import Embedder, format_query
from lantern.search import scope as scope_mod
from lantern.store import db
from lantern.textnorm import excerpt, fts_query, term_coverage

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
    coverage: float = 1.0  # keyword hits: share of query terms matched (used to weight fusion)
    page: int | None = None  # PDF page
    full_text: str = field(default="", repr=False, compare=False)


def _hits(conn: sqlite3.Connection, scored: list[tuple[int, float]], query: str | None = None) -> list[Hit]:
    if not scored:
        return []
    marks = ",".join("?" * len(scored))
    rows = {
        r["id"]: r
        for r in conn.execute(
            f"SELECT c.id, c.start_line, c.end_line, c.symbol, c.text, c.kind, c.start_ms, c.end_ms, c.page, f.path "
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
        preview = excerpt(query, text or "") if query else " ".join((text or "").split())[:160]
        out.append(
            Hit(
                chunk_id=cid,
                path=r["path"],
                start_line=r["start_line"],
                end_line=r["end_line"],
                symbol=r["symbol"],
                score=score,
                preview=preview,
                kind=r["kind"],
                start_ms=r["start_ms"],
                end_ms=r["end_ms"],
                page=r["page"],
                full_text=text or "",
            )
        )
    return out


def _transcript_at(conn: sqlite3.Connection, chunk_id: int, start_ms: int | None, end_ms: int | None) -> str:
    """Preview for an audio-window or frame match: the transcript spoken in the same time range, or (when matched at a
    silent moment, e.g. by file name) the nearest transcript or screen-text passage of the recording."""
    if start_ms is None:
        return ""
    rows = conn.execute(
        "SELECT t.text FROM chunks a JOIN chunks t ON t.file_id = a.file_id AND t.kind = 'transcript' "
        "WHERE a.id = ? AND t.end_ms > ? AND t.start_ms < ? ORDER BY t.start_ms",
        (chunk_id, start_ms, end_ms),
    ).fetchall()
    if rows:
        return " ".join(r[0] for r in rows)
    row = conn.execute(
        "SELECT t.text FROM chunks a JOIN chunks t ON t.file_id = a.file_id AND t.kind IN ('transcript', 'screen') "
        "WHERE a.id = ? ORDER BY (t.kind != 'transcript'), ABS(COALESCE(t.start_ms, 0) - ?) LIMIT 1",
        (chunk_id, start_ms),
    ).fetchone()
    return row[0] if row else ""


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


# Audio windows (EmbeddingGemma's audio tower) are "hubs": some sit close to almost any query ("sea", "elephant" and
# "forest" all found the same music clip first, all within 0.62–0.71). Each window's typical closeness to generic
# queries is subtracted (CSLS-style) and only windows that stand out by AUDIO_MIN_MARGIN count; measured on the
# user's index: "rain" +0.017, a talk about a dashboard +0.014, "sea" at most +0.003 (docs/DECISIONS.md).
HUB_REFERENCES = (
    "people talking",
    "a man speaking",
    "music playing",
    "piano",
    "guitar",
    "a car engine",
    "birds singing",
    "dog barking",
    "wind",
    "rain",
    "applause",
    "a phone ringing",
    "typing on a keyboard",
    "a lecture",
    "a meeting",
    "kitchen sounds",
    "traffic",
    "a crowd",
    "silence",
    "footsteps",
    "rozmowa ludzi",
    "muzyka",
    "wykład",
    "spotkanie",
    "szum",
    "śpiew",
    "telefon dzwoni",
    "samochód",
    "ptaki",
    "kot miauczy",
    "a baby crying",
    "water running",
    "a train",
    "thunder",
    "an interview",
    "a podcast",
    "a song",
    "drums",
    "laughter",
    "a door closing",
)
HUB_K = 10
AUDIO_MIN_MARGIN = 0.01
_hub_refs: dict = {}


def _hub_references(embedder: Embedder) -> np.ndarray:
    key = embedder.model_info()  # vectors of one model only
    if key not in _hub_refs:
        _hub_refs[key] = embedder.embed_text([format_query(q) for q in HUB_REFERENCES]).astype(np.float32)
    return _hub_refs[key]


def audio_margin(score: float, vector: np.ndarray, refs: np.ndarray) -> float:
    """How far a window's score stands above its usual closeness to generic queries (mean of its HUB_K best)."""
    return score - float(np.sort(refs @ vector)[-HUB_K:].mean())


def _drop_hub_audio(conn: sqlite3.Connection, embedder: Embedder, table: str, hits: list[Hit]) -> list[Hit]:
    audio = {h.chunk_id: h.score for h in hits if h.kind == "audio"}
    if not audio:
        return hits
    refs = _hub_references(embedder)
    keep = {cid for cid, v in _vectors(conn, table, list(audio)).items()
            if audio_margin(audio[cid], v, refs) >= AUDIO_MIN_MARGIN}  # fmt: skip
    return [h for h in hits if h.kind != "audio" or h.chunk_id in keep]


def _knn(conn, table: str, qv: np.ndarray, k: int, scope: "str | scope_mod.Scope | None") -> list[tuple[int, float]]:
    """(chunk id, cosine) of the nearest vectors; within a scope (search/scope.py) every vector of a small scope is
    scored exactly, a large one takes a wider KNN pool kept to the scope."""
    if scope is not None:
        ids = scope_mod.chunk_ids(conn, scope)
        if len(ids) <= scope_mod.EXACT_SCOPE_MAX:
            vecs = _vectors(conn, table, ids)
            ranked = sorted(((c, float(v @ qv)) for c, v in vecs.items()), key=lambda kv: -kv[1])
            return ranked[:k]
        allowed = set(ids)
        rows = conn.execute(
            f"SELECT chunk_id, distance FROM {table} WHERE embedding MATCH ? AND k = ?", (qv.tobytes(), 4096)
        ).fetchall()
        return [(r[0], 1.0 - r[1]) for r in rows if r[0] in allowed][:k]
    rows = conn.execute(
        f"SELECT chunk_id, distance FROM {table} WHERE embedding MATCH ? AND k = ?", (qv.tobytes(), k)
    ).fetchall()
    return [(r[0], 1.0 - r[1]) for r in rows]


def vector_search(
    conn: sqlite3.Connection,
    embedder: Embedder,
    query: str,
    limit: int = 10,
    scope: "str | scope_mod.Scope | None" = None,
) -> list[Hit]:
    model_id = db.find_model(conn, embedder.model_info())
    if model_id is None:  # nothing indexed with this model yet
        return []
    qv = embedder.embed_text([format_query(query)])[0].astype(np.float32)
    hits = _hits(conn, _knn(conn, db.vec_table(model_id), qv, limit * 5, scope), query)
    return _best_per_file(_drop_hub_audio(conn, embedder, db.vec_table(model_id), hits), limit)


def keyword_search(
    conn: sqlite3.Connection, query: str, limit: int = 10, scope: "str | scope_mod.Scope | None" = None
) -> list[Hit]:
    expr = fts_query(query)
    if expr is None:
        return []
    if scope is not None:  # joined, so SQLite filters the matches by file (a rowid IN subquery took ~700 ms)
        where, params = scope_mod.condition(scope)
        rows = conn.execute(
            f"SELECT chunks_fts.rowid, bm25(chunks_fts, {BM25_WEIGHTS}) AS s FROM chunks_fts "
            "JOIN chunks c ON c.id = chunks_fts.rowid JOIN files f ON f.id = c.file_id "
            f"WHERE chunks_fts MATCH ? AND {where} ORDER BY s LIMIT ?",
            (expr, *params, limit * 5),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT rowid, bm25(chunks_fts, {BM25_WEIGHTS}) AS s FROM chunks_fts "
            "WHERE chunks_fts MATCH ? ORDER BY s LIMIT ?",
            (expr, limit * 5),
        ).fetchall()
    hits = _best_per_file(_hits(conn, [(r[0], -r[1]) for r in rows], query), limit)
    return [replace(h, coverage=term_coverage(query, f"{h.full_text} {h.path} {h.symbol or ''}")) for h in hits]


PHOTO_MATCH_Z = 1.0  # per model: a photo must score at least one standard deviation above the candidate pool


def _vectors(conn: sqlite3.Connection, table: str, chunk_ids: list[int]) -> dict[int, np.ndarray]:
    """Stored vectors by chunk id. vec0 keeps vectors in blocks of 1024 and copies a whole block for every vector it
    returns (~94 µs each; `chunk_id IN (…)` is slower still), so they are read straight from its shadow tables: the
    block and offset of each id from `<table>_rowids`, then 3 KB from the block's blob (6.8k vectors: 16 ms instead
    of 640 ms, byte-identical). If a future sqlite-vec lays its tables out differently, plain lookups are used."""
    try:
        return _vectors_from_blocks(conn, table, chunk_ids)
    except (sqlite3.Error, ValueError):
        out = {}
        for c in chunk_ids:
            row = conn.execute(f"SELECT embedding FROM {table} WHERE chunk_id = ?", (c,)).fetchone()
            if row is not None:
                out[c] = np.frombuffer(row[0], dtype=np.float32)
        return out


def _vectors_from_blocks(conn: sqlite3.Connection, table: str, chunk_ids: list[int]) -> dict[int, np.ndarray]:
    size = _dim(conn, table) * 4
    located: list[tuple[int, int, int]] = []
    for i in range(0, len(chunk_ids), 900):
        part = chunk_ids[i : i + 900]
        located += conn.execute(
            f"SELECT rowid, chunk_id, chunk_offset FROM {table}_rowids WHERE rowid IN ({','.join('?' * len(part))})",
            part,
        ).fetchall()
    out: dict[int, np.ndarray] = {}
    blobs = {}
    try:
        for cid, block, offset in sorted(located, key=lambda r: (r[1], r[2])):
            blob = blobs.get(block)
            if blob is None:
                blob = blobs[block] = conn.blobopen(f"{table}_vector_chunks00", "vectors", block, readonly=True)
            blob.seek(offset * size)
            data = blob.read(size)
            if len(data) != size:
                raise ValueError("unexpected vec0 block layout")
            out[cid] = np.frombuffer(data, dtype=np.float32)
    finally:
        for blob in blobs.values():
            blob.close()
    return out


def _dim(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()
    m = re.search(r"FLOAT\[(\d+)\]", row[0] if row else "")
    if not m:
        raise ValueError("vector size not found")
    return int(m.group(1))


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone() is not None


def image_search(
    conn: sqlite3.Connection,
    embedder: Embedder,
    query: str,
    limit: int = 10,
    lang: str = "en",
    query_embedder=None,
    scope: "str | scope_mod.Scope | None" = None,
    only: "scope_mod.Scope | None" = None,
) -> list[Hit]:
    """Photos and video frames, ranked by both photo models. Candidates from each model's KNN are pooled; for every
    candidate both similarities are computed exactly, z-normalised per model over the pool and summed (raw
    similarities of two models are not comparable). Without the second model, EmbeddingGemma alone.

    `only` (people words) keeps the photos that satisfy it, but the z-scores are measured against the photos within
    `scope` alone: a pool of just the one or two matching photos made their z-score meaningless (a single photo is
    always 0), so "women in Lisbon" lost the photo "woman in Lisbon" found."""
    from lantern.embedding.base import format_image_query

    lists: list[tuple[str, np.ndarray]] = []
    eg_id = db.find_model(conn, embedder.model_info())
    if eg_id is not None and _table_exists(conn, db.vec_table(eg_id, images=True)):
        lists.append((db.vec_table(eg_id, images=True), embedder.embed_text([format_image_query(query, lang)])[0]))
    if query_embedder is not None:
        sig_id = db.find_model(conn, query_embedder.model_info())
        if sig_id is not None and _table_exists(conn, db.vec_table(sig_id, images=True)):
            lists.append((db.vec_table(sig_id, images=True), query_embedder.embed_queries([query])[0]))
    if not lists:
        return []

    pool: set[int] = set()
    allowed: set[int] | None = None
    for table, qv in lists:
        pool.update(c for c, _ in _knn(conn, table, qv.astype(np.float32), limit * 5, scope))
        if only is not None:  # the nearest photos that satisfy it are always scored (their z against the full pool)
            near = {c for c, _ in _knn(conn, table, qv.astype(np.float32), limit * 5, scope_mod.Scope.all(scope, only))}
            allowed = near if allowed is None else allowed | near
            pool |= near
    ids = sorted(pool)
    if not ids:
        return []
    total = np.zeros(len(ids), dtype=np.float32)
    for table, qv in lists:
        vecs = _vectors(conn, table, ids)
        scores = np.array([float(vecs[c] @ qv) if c in vecs else np.nan for c in ids], dtype=np.float32)
        valid = ~np.isnan(scores)
        if valid.sum() > 1 and scores[valid].std() > 0:
            z = (scores - scores[valid].mean()) / scores[valid].std()
        else:
            z = np.zeros_like(scores)
        total += np.where(valid, z, 0.0)  # a chunk missing from one model counts as average there
    # Only candidates clearly above the pool average count as photo matches; the rest of the pool is just "nearest of
    # the remaining photos" and would add a photo-list vote to every query (e.g. any video's generic frames).
    min_z = PHOTO_MATCH_Z * len(lists)  # the fused score is a sum of per-model z-scores
    ranked = sorted(((c, z) for c, z in zip(ids, total.tolist(), strict=True)
                     if z >= min_z and (allowed is None or c in allowed)), key=lambda kv: -kv[1])  # fmt: skip
    return _best_per_file(_hits(conn, ranked, query), limit)

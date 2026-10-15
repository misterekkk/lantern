"""Hybrid search: vector and keyword candidate lists fused with Reciprocal Rank Fusion at file level.

Embeddings miss exact identifiers and error messages; BM25 misses paraphrases and other languages. RRF needs no
score calibration: score(file) = sum over lists of 1 / (k + rank). Every search is logged to search_events with
per-list ranks and scores (training data for a future reranker).
"""

import json
import sqlite3
import time
from dataclasses import asdict, dataclass

from lantern.embedding.base import Embedder
from lantern.search.basic import Hit, keyword_search, vector_search
from lantern.store import db

RRF_K = 60
CANDIDATES = 50  # files per list before fusion
# A keyword hit counts in the fusion in proportion to the share of query terms it matches, and not at all below
# this share: one incidental shared word (often across languages) must not outrank the top vector match.
MIN_KEYWORD_COVERAGE = 0.5


@dataclass(frozen=True)
class Result:
    path: str
    chunk_id: int
    start_line: int | None
    end_line: int | None
    symbol: str | None
    preview: str
    score: float  # RRF
    vector_rank: int | None
    vector_score: float | None
    keyword_rank: int | None
    keyword_score: float | None
    keyword_coverage: float | None = None
    kind: str = "text"
    start_ms: int | None = None
    end_ms: int | None = None


@dataclass(frozen=True)
class SearchResponse:
    results: list[Result]
    event_id: int | None
    vector_ms: float
    keyword_ms: float


def fuse(vector: list[Hit], keyword: list[Hit], limit: int, k: int = RRF_K) -> list[Result]:
    vrank = {h.path: (i + 1, h) for i, h in enumerate(vector)}
    krank = {h.path: (i + 1, h) for i, h in enumerate(keyword)}
    fused = []
    for path in vrank.keys() | krank.keys():
        v, kw = vrank.get(path), krank.get(path)
        kw_weight = kw[1].coverage if kw and kw[1].coverage >= MIN_KEYWORD_COVERAGE else 0.0
        score = (1 / (k + v[0]) if v else 0.0) + (kw_weight / (k + kw[0]) if kw else 0.0)
        if score == 0.0:
            continue
        # Show the chunk from the list where this file ranks better (vector wins ties).
        best = v[1] if v and (not kw or v[0] <= kw[0]) else kw[1]
        fused.append(
            Result(
                path=path,
                chunk_id=best.chunk_id,
                start_line=best.start_line,
                end_line=best.end_line,
                symbol=best.symbol,
                preview=best.preview,
                score=score,
                vector_rank=v[0] if v else None,
                vector_score=round(v[1].score, 4) if v else None,
                keyword_rank=kw[0] if kw else None,
                keyword_score=round(kw[1].score, 4) if kw else None,
                keyword_coverage=round(kw[1].coverage, 3) if kw else None,
                kind=best.kind,
                start_ms=best.start_ms,
                end_ms=best.end_ms,
            )
        )
    fused.sort(key=lambda r: (-r.score, r.vector_rank or 10**6))
    return fused[:limit]


def log_search(conn: sqlite3.Connection, query: str, results: list[Result]) -> int:
    payload = [{"rank": i + 1, **asdict(r)} for i, r in enumerate(results)]
    for item in payload:
        item.pop("preview")  # features only, no file content in the log
    cur = conn.execute(
        "INSERT INTO search_events(ts, query, results_json) VALUES (?, ?, ?)",
        (db.now(), query, json.dumps(payload, ensure_ascii=False)),
    )
    return cur.lastrowid


def search(
    conn: sqlite3.Connection, embedder: Embedder, query: str, limit: int = 10, log: bool = True
) -> SearchResponse:
    t0 = time.perf_counter()
    vector = vector_search(conn, embedder, query, CANDIDATES)
    t1 = time.perf_counter()
    keyword = keyword_search(conn, query, CANDIDATES)
    t2 = time.perf_counter()
    results = fuse(vector, keyword, limit)
    event_id = log_search(conn, query, results) if log else None
    return SearchResponse(results, event_id, (t1 - t0) * 1000, (t2 - t1) * 1000)


def last_event(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT id, query, results_json FROM search_events ORDER BY id DESC LIMIT 1").fetchone()


def mark_opened(conn: sqlite3.Connection, event_id: int, chunk_id: int) -> None:
    conn.execute("UPDATE search_events SET opened_chunk_id = ? WHERE id = ?", (chunk_id, event_id))


def clear_history(conn: sqlite3.Connection) -> int:
    return conn.execute("DELETE FROM search_events").rowcount

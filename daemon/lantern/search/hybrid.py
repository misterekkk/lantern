"""Hybrid search: vector and keyword candidate lists fused with Reciprocal Rank Fusion at file level.

Embeddings miss exact identifiers and error messages; BM25 misses paraphrases and other languages. RRF needs no
score calibration: score(file) = sum over lists of 1 / (k + rank). Every search is logged to search_events with
per-list ranks and scores (training data for a future reranker).
"""

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field

from lantern.embedding.base import Embedder
from lantern.search.basic import Hit, image_search, keyword_search, vector_search
from lantern.store import db

RRF_K = 60
CANDIDATES = 50  # files per list before fusion
# A keyword hit counts in the fusion in proportion to the share of query terms it matches, and not at all below
# this share: one incidental shared word (often across languages) must not outrank the top vector match.
MIN_KEYWORD_COVERAGE = 0.5
# Photos and documents are different things: one fused ranking always compromises (a code file that contains the
# word "pies" outranks a photo of a dog). Results come in two sections, like Spotlight: photos/video frames and files.
# The section whose list is more confident goes first. Confidence = how clearly the best hit stands out from the
# bulk of its candidates (several equally good hits, e.g. three dog photos, still count as confident).
# See DECISIONS.md, Phase 7.
PHOTO_SECTION_SIZE = 4
MIN_LIST_WEIGHT = 0.25
TEXT_GAP_LOW, TEXT_GAP_HIGH = 0.02, 0.08  # cosine gap (top - median of candidates): flat .. clear
PHOTO_Z_LOW, PHOTO_Z_HIGH = 1.0, 4.0  # per-model z of the top photo against the photo candidate pool


def _ramp(x: float, low: float, high: float) -> float:
    return max(MIN_LIST_WEIGHT, min(1.0, MIN_LIST_WEIGHT + (1 - MIN_LIST_WEIGHT) * (x - low) / (high - low)))


def text_list_confidence(hits: list[Hit]) -> float:
    if len(hits) < 3:
        return 0.6  # too few candidates to tell
    scores = sorted((h.score for h in hits), reverse=True)
    median = scores[len(scores) // 2]
    return _ramp(scores[0] - median, TEXT_GAP_LOW, TEXT_GAP_HIGH)


def photo_list_confidence(hits: list[Hit], models: int) -> float:
    if not hits:
        return 0.0
    return _ramp(hits[0].score / max(1, models), PHOTO_Z_LOW, PHOTO_Z_HIGH)


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
    image_rank: int | None = None  # photos / video frames (both photo models, z-score fused)
    image_score: float | None = None
    kind: str = "text"
    start_ms: int | None = None
    end_ms: int | None = None


@dataclass(frozen=True)
class SearchResponse:
    results: list[Result]
    event_id: int | None
    vector_ms: float
    keyword_ms: float
    image_ms: float = 0.0
    photos: list[Result] = field(default_factory=list)  # photos and video frames section
    photos_first: bool = False


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


def photo_results(image: list[Hit], keyword: list[Hit], limit: int = PHOTO_SECTION_SIZE) -> list[Result]:
    """Photos section: photos matched by content, then photos whose name matches the query."""
    by_name = [h for h in keyword if h.kind == "image" and h.coverage >= MIN_KEYWORD_COVERAGE]
    seen = {h.path for h in image}
    hits = [(h, True) for h in image] + [(h, False) for h in by_name if h.path not in seen]
    return [
        Result(
            path=h.path,
            chunk_id=h.chunk_id,
            start_line=None,
            end_line=None,
            symbol=None,
            preview="",
            score=round(h.score, 4),
            vector_rank=None,
            vector_score=None,
            keyword_rank=None if visual else i + 1,
            keyword_score=None if visual else round(h.score, 4),
            image_rank=i + 1 if visual else None,
            image_score=round(h.score, 4) if visual else None,
            kind=h.kind,
            start_ms=h.start_ms,
            end_ms=h.end_ms,
        )  # fmt: skip
        for i, (h, visual) in enumerate(hits[:limit])
    ]


def log_search(conn: sqlite3.Connection, query: str, results: list[Result], photos: list[Result] = ()) -> int:
    payload = [{"rank": i + 1, "section": "files", **asdict(r)} for i, r in enumerate(results)]
    payload += [{"rank": i + 1, "section": "photos", **asdict(r)} for i, r in enumerate(photos)]
    for item in payload:
        item.pop("preview")  # features only, no file content in the log
    cur = conn.execute(
        "INSERT INTO search_events(ts, query, results_json) VALUES (?, ?, ?)",
        (db.now(), query, json.dumps(payload, ensure_ascii=False)),
    )
    return cur.lastrowid


def search(
    conn: sqlite3.Connection,
    embedder: Embedder,
    query: str,
    limit: int = 10,
    log: bool = True,
    lang: str = "en",
    query_embedder=None,
) -> SearchResponse:
    """query_embedder: text side of the second photo model (SigLIP 2), when loaded; lang: system language, used to
    give short photo queries a language cue."""
    t0 = time.perf_counter()
    vector = vector_search(conn, embedder, query, CANDIDATES)
    t1 = time.perf_counter()
    keyword = keyword_search(conn, query, CANDIDATES)
    t2 = time.perf_counter()
    image = image_search(conn, embedder, query, CANDIDATES, lang, query_embedder)
    t3 = time.perf_counter()
    # Photos only appear in the photos section (video frames stay with their video file in the files section).
    results = fuse([h for h in vector if h.kind != "image"], [h for h in keyword if h.kind != "image"], limit)
    photos = photo_results(image, keyword)
    models = 2 if query_embedder is not None else 1
    photos_first = bool(image) and photo_list_confidence(image, models) > text_list_confidence(vector)
    event_id = log_search(conn, query, results, photos) if log else None
    return SearchResponse(
        results,
        event_id,
        (t1 - t0) * 1000,
        (t2 - t1) * 1000,
        (t3 - t2) * 1000,
        photos=photos,
        photos_first=photos_first,
    )


def last_event(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT id, query, results_json FROM search_events ORDER BY id DESC LIMIT 1").fetchone()


def mark_opened(conn: sqlite3.Connection, event_id: int, chunk_id: int) -> None:
    conn.execute("UPDATE search_events SET opened_chunk_id = ? WHERE id = ?", (chunk_id, event_id))


def clear_history(conn: sqlite3.Connection) -> int:
    return conn.execute("DELETE FROM search_events").rowcount

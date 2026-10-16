"""Hybrid search: vector and keyword candidate lists fused with Reciprocal Rank Fusion at file level.

Embeddings miss exact identifiers and error messages; BM25 misses paraphrases and other languages. RRF needs no
score calibration: score(file) = sum over lists of 1 / (k + rank). Every search is logged to search_events with
per-list ranks and scores (training data for a future reranker).
"""

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from lantern.answers import Answer, instant_answer
from lantern.dictionary import Definition as WordDefinition
from lantern.dictionary import define
from lantern.embedding.base import Embedder
from lantern.search.apps import AppHit, ShortcutHit, app_search, shortcut_search
from lantern.search.basic import Hit, image_search, keyword_search, vector_search
from lantern.search.settings import SettingHit, settings_search
from lantern.search.spelling import correct
from lantern.search.suggest import completion
from lantern.search.symbols import Definition, looks_like_code, symbol_search
from lantern.store import db
from lantern.textnorm import STOPWORDS, query_terms, term_coverage

RRF_K = 60
CANDIDATES = 50  # files per list before fusion
# A keyword hit counts in the fusion in proportion to the share of query terms it matches, and not at all below
# this share: one incidental shared word (often across languages) must not outrank the top vector match.
MIN_KEYWORD_COVERAGE = 0.5
# Photos and documents are different things: one fused ranking always compromises (a code file that contains the
# word "pies" outranks a photo of a dog). Results come in sections, like Spotlight: definitions, photos (with video
# frames, which keep their timestamp) and files.
# The section whose list is more confident goes first. Confidence = how clearly the best hit stands out from the
# bulk of its candidates (several equally good hits, e.g. three dog photos, still count as confident).
# See DECISIONS.md, Phase 7.
PHOTO_SECTION_SIZE = 4
TYPING_WINDOW_S = 5  # history: a longer query typed within this many seconds replaces the shorter one
# Filter pills in the panel: result categories by chunk kind, in a fixed order (stable keyboard shortcuts).
CATEGORIES = {
    "apps": {"app"},  # applications (search/apps.py), first like Spotlight's ⌘1
    "settings": {"setting"},  # System Settings panes and settings (search/settings.py)
    "shortcuts": {"shortcut"},  # the user's shortcuts (Shortcuts app)
    "code": {"code"},
    "docs": {"text"},
    "pdf": {"pdf"},
    "photos": {"image", "ocr"},
    "media": {"audio", "transcript", "frame", "screen"},
}
MIN_LIST_WEIGHT = 0.25
TEXT_GAP_LOW, TEXT_GAP_HIGH = 0.02, 0.08  # cosine gap (top - median of candidates): flat .. clear
PHOTO_Z_LOW, PHOTO_Z_HIGH = 1.0, 4.0  # per-model z of the top photo against the photo candidate pool
# One scale for every section (DECISIONS.md, "Ordering sections"): answers and name matches first, then files that
# contain every word of the query, then the web row (0.5, added by the app), then guesses by meaning.
WEB_ROW_SCORE = 0.5
PHOTO_SURE_Z = 7.0  # summed z of both photo models: a confident visual match (real photo queries: 7.6–8.5)
PHOTO_SHOW_Z = 5.0  # below it, photos that only look a bit like the query are hidden ("co tam": 3.7); 5–7 sit low
FILE_AGREE_RANK = 10  # a file found by every word must also rank this high by meaning to count as a sure answer
FILE_MEANING_STRONG = 0.78  # top text vector score that counts as a clear meaning match (with a clear gap)
_NAME_SCORES = {"exact": 0.97, "prefix": 0.93, "word": 0.85, "initials": 0.85, "typo": 0.8, "keyword": 0.75,
                "leading": 0.76, "contains": 0.74,
                "words": 0.8, "meaning": 0.42, "partial": 0.6, "category": 0.6}  # fmt: skip


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
    page: int | None = None  # PDF page
    text_rank: int | None = None  # photos: rank by the text read from the image (OCR), words or meaning


@dataclass(frozen=True)
class SearchResponse:
    results: list[Result]
    event_id: int | None
    vector_ms: float
    keyword_ms: float
    image_ms: float = 0.0
    photos: list[Result] = field(default_factory=list)  # photos and video frames section
    photos_first: bool = False
    definitions: list[Definition] = field(default_factory=list)  # code definitions ("def add_two(x, y)")
    symbol_ms: float = 0.0
    categories: list[str] = field(default_factory=list)  # categories with matches (filter pills), CATEGORIES order
    category: str | None = None  # the active filter
    completion: str | None = None  # inline completion of the query (panel: accepted with →)
    apps: list[AppHit] = field(default_factory=list)  # applications section
    apps_first: bool = False  # an app matched by name: it is the top hit; meaning-only apps go last
    settings: list[SettingHit] = field(default_factory=list)  # System Settings section
    settings_first: bool = False  # matched by name or words: right after apps; meaning-only: last
    settings_app_name: str | None = None  # "Ustawienia systemowe": macOS's own name for the app, in its language
    shortcuts: list[ShortcutHit] = field(default_factory=list)  # the user's shortcuts, after apps
    answer: Answer | None = None  # arithmetic, units, world time: always the top line
    definition: WordDefinition | None = None  # dictionary entry for a single word: near the end
    definition_first: bool = False  # no file contains the word: the definition is the better answer
    section_scores: dict[str, float] = field(default_factory=dict)  # 0..1 per section, on one scale (see top)
    corrected_query: str | None = None  # the query actually searched, when a spelling guess replaced a word

    def sections(self) -> list[tuple[str, list]]:
        """Non-empty sections in display order: by section_scores when set (one scale for every kind), otherwise
        the fixed order below."""
        if self.section_scores:
            named = {"answer": [self.answer] if self.answer else [], "apps": self.apps, "shortcuts": self.shortcuts,
                     "settings": self.settings, "definitions": self.definitions, "photos": self.photos,
                     "files": self.results, "definition": [self.definition] if self.definition else []}  # fmt: skip
            order = sorted(named, key=lambda n: -self.section_scores.get(n, 0.0))  # stable: ties keep this order
            return [(n, named[n]) for n in order if named[n] and n in self.section_scores]
        media = [("photos", self.photos), ("files", self.results)]
        order = [("definitions", self.definitions)] + (media if self.photos_first else media[::-1])
        first = [("answer", [self.answer] if self.answer else [])]
        first += [("apps", self.apps)] if self.apps_first else []
        first += [("shortcuts", self.shortcuts)]
        first += [("definition", [self.definition])] if self.definition and self.definition_first else []
        first += [("settings", self.settings)] if self.settings_first else []
        last = [] if self.settings_first else [("settings", self.settings)]
        last += [] if self.apps_first else [("apps", self.apps)]
        last += [("definition", [self.definition])] if self.definition and not self.definition_first else []
        order = [*first, *order, *last]
        return [(name, items) for name, items in order if items]


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
                page=best.page,
            )
        )
    fused.sort(key=lambda r: (-r.score, r.vector_rank or 10**6))
    return fused[:limit]


OCR_VECTOR_TOP = 5  # text read from an image counts by meaning only among the very best vector hits


def photo_results(
    image: list[Hit],
    keyword: list[Hit],
    vector: list[Hit] = (),
    limit: int = PHOTO_SECTION_SIZE,
    k: int = RRF_K,
    query: str = "",
) -> list[Result]:
    """Photos section, fused with RRF from three lists: what the photo shows (both photo models), the words of the
    text read from it (OCR, or its file name), and the meaning of that text (only near the top of the vector list,
    so screenshots full of text do not join every query)."""
    visual = {h.path: (i + 1, h) for i, h in enumerate(image)}
    words = [h for h in keyword if h.kind in ("image", "ocr") and h.coverage >= MIN_KEYWORD_COVERAGE]
    by_words = {h.path: (i + 1, h) for i, h in enumerate(words)}
    # By meaning only for descriptive queries: the meaning of a number or code says nothing about which image holds it.
    lookup = looks_like_code(query) or any(c.isdigit() for c in query)
    meaning = [] if lookup else [h for h in vector[:OCR_VECTOR_TOP] if h.kind == "ocr"]
    by_meaning = {h.path: (i + 1, h) for i, h in enumerate(meaning)}
    fused = []
    for path in visual.keys() | by_words.keys() | by_meaning.keys():
        v, w, m = visual.get(path), by_words.get(path), by_meaning.get(path)
        score = (1 / (k + v[0]) if v else 0) + (w[1].coverage / (k + w[0]) if w else 0) + (1 / (k + m[0]) if m else 0)
        text_hit = w or m
        shown = text_hit[1] if text_hit and text_hit[1].kind == "ocr" else (v or text_hit)[1]
        frame = v[1] if v and v[1].kind == "frame" else None  # a video frame keeps its moment
        fused.append(Result(
            path=path, chunk_id=shown.chunk_id, start_line=None, end_line=None, symbol=None,
            preview=shown.preview if shown.kind == "ocr" else "", score=round(score, 5),
            vector_rank=None, vector_score=None, keyword_rank=w[0] if w else None,
            keyword_score=round(w[1].score, 4) if w else None, keyword_coverage=round(w[1].coverage, 3) if w else None,
            image_rank=v[0] if v else None, image_score=round(v[1].score, 4) if v else None,
            kind="frame" if frame else "image", text_rank=(m[0] if m else None) if not w else w[0],
            start_ms=frame.start_ms if frame else None, end_ms=frame.end_ms if frame else None,
        ))  # fmt: skip
    fused.sort(key=lambda r: -r.score)
    return fused[:limit]


def _categories(vector: list[Hit], keyword: list[Hit], image: list[Hit], definitions: list) -> list[str]:
    """Categories that have matches (only these get a pill). Keyword hits count from half the query's words."""
    kinds = {h.kind for h in vector[:20]} | {h.kind for h in keyword if h.coverage >= MIN_KEYWORD_COVERAGE}
    kinds |= {h.kind for h in image}
    if definitions:
        kinds.add("code")
    return [name for name, members in CATEGORIES.items() if kinds & members]


def _section_scores(
    query,
    results,
    file_vector,
    keyword,
    image,
    photos,
    exact_text,
    apps,
    settings,
    shortcuts,
    definitions,
    answer,
    word_definition,
) -> dict[str, float]:
    """Each non-empty section's confidence on one 0..1 scale, so sections of different kinds can be ordered and
    weak ones dropped (measured on local and web-like queries: DECISIONS.md, "Ordering sections")."""
    scores: dict[str, float] = {}
    if answer:
        scores["answer"] = 1.0
    for name, hits in (("apps", apps), ("settings", settings), ("shortcuts", shortcuts)):
        if hits:
            scores[name] = _NAME_SCORES.get(hits[0].match, 0.5)
    if settings and settings[0].match == "words" and len(query.split()) == 1 and len(query.strip()) < 6:
        scores["settings"] = 0.55  # one short word ("disc") starts words in many settings: weak evidence
    if definitions:
        scores["definitions"] = {"exact": 0.9, "prefix": 0.8}.get(definitions[0].match, 0.6)
    terms = query_terms(query)
    content = [t for t, _ in terms if t.replace(" ", "") and t.split()[0] not in STOPWORDS]
    # Files are a sure answer when both lists agree on one: it contains every content word AND its meaning ranks near
    # the top. "Every word somewhere" alone is weak in a big index ("cena" + "dolarów" in some statistics PDF).
    whole = bool(content) and any(
        (r.keyword_coverage or 0) >= 1.0 and r.vector_rank is not None and r.vector_rank <= FILE_AGREE_RANK
        for r in results[:5]
    )
    clear_meaning = (
        bool(file_vector) and file_vector[0].score >= FILE_MEANING_STRONG and (text_list_confidence(file_vector) >= 0.9)
    )
    if results:
        scores["files"] = 0.72 if whole else 0.6 if clear_meaning else 0.35
    if photos:
        # Every word of the query in the text read from the image, or in its file name ("hats_simulation.gif").
        by_text = any(
            (h.kind == "ocr" and h.coverage >= 1.0)
            or (h.kind == "image" and term_coverage(query, Path(h.path).stem.replace("_", " ")) >= 1.0)
            for h in keyword[:5]
        )
        top_z = image[0].score if image else 0.0
        if exact_text:
            scores["photos"] = 0.85
        elif by_text:
            scores["photos"] = 0.7
        elif top_z >= PHOTO_SURE_Z:  # the surer the photo models, the higher (z 7 -> 0.66, z 9 -> 0.82)
            scores["photos"] = 0.66 + 0.16 * min(1.0, (top_z - PHOTO_SURE_Z) / 2.0)
        elif top_z >= PHOTO_SHOW_Z:  # plausible (a small thumbnail, a busy photo): shown, below the web row
            scores["photos"] = 0.45
    if word_definition:
        scores["definition"] = 0.64 if not any(r.keyword_rank for r in results) else 0.3
    return scores


def _settings_app_name(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT name FROM apps WHERE path LIKE '%/System Settings.app'").fetchone()
    return row["name"] if row else None


def _log_item(section: str, item: Result | Definition | AppHit | SettingHit) -> dict:
    if isinstance(item, AppHit):  # chunk_id 0: an application or a setting, not a passage of a file
        return {"section": section, "path": item.path, "chunk_id": 0, "start_line": None, "kind": "app",
                "match": item.match, "score": item.score, "start_ms": None}  # fmt: skip
    if isinstance(item, ShortcutHit):
        return {"section": section, "path": f"shortcut:{item.id}", "chunk_id": 0, "start_line": None,
                "kind": "shortcut", "match": item.match, "score": item.score, "start_ms": None}  # fmt: skip
    if isinstance(item, Answer | WordDefinition):  # computed on the spot: only the kind is logged
        return {"section": section, "path": "", "chunk_id": 0, "start_line": None, "kind": section, "start_ms": None}
    if isinstance(item, SettingHit):
        return {"section": section, "path": item.url, "chunk_id": 0, "start_line": None, "kind": "setting",
                "symbol": item.title, "match": item.match, "score": item.score, "start_ms": None}  # fmt: skip
    if isinstance(item, Definition):
        return {
            "section": section, "path": item.path, "chunk_id": item.chunk_id, "start_line": item.line,
            "end_line": item.end_line, "symbol": item.name, "kind": item.kind, "match": item.match,
            "score": item.score, "start_ms": None,
        }  # fmt: skip
    entry = {"section": section, **asdict(item)}
    entry.pop("preview")  # features only, no file content in the log
    return entry


def log_search(conn: sqlite3.Connection, query: str, sections: list[tuple[str, list]]) -> int:
    """Results in display order, so `lantern open <n>` opens the n-th result as shown."""
    payload = []
    for section, items in sections:
        payload += [{"rank": i + 1, **_log_item(section, item)} for i, item in enumerate(items)]
    data = json.dumps(payload, ensure_ascii=False)
    # Search-as-you-type: a query that extends the previous one within a few seconds (and nothing was opened yet)
    # replaces it, so the history keeps "postgres", not "po", "pos", "post"…
    last = conn.execute("SELECT id, ts, query, opened_chunk_id FROM search_events ORDER BY id DESC LIMIT 1").fetchone()
    if (last and last["opened_chunk_id"] is None and db.now() - last["ts"] <= TYPING_WINDOW_S
            and query.casefold().startswith(last["query"].casefold())):  # fmt: skip
        conn.execute("UPDATE search_events SET ts = ?, query = ?, results_json = ? WHERE id = ?",
                     (db.now(), query, data, last["id"]))  # fmt: skip
        return last["id"]
    cur = conn.execute("INSERT INTO search_events(ts, query, results_json) VALUES (?, ?, ?)", (db.now(), query, data))
    return cur.lastrowid


def search(
    conn: sqlite3.Connection,
    embedder: Embedder,
    query: str,
    limit: int = 10,
    log: bool = True,
    lang: str = "en",
    query_embedder=None,
    category: str | None = None,
    spelling: dict[str, list[str]] | None = None,
) -> SearchResponse:
    """query_embedder: text side of the second photo model (SigLIP 2), when loaded; lang: system language, used to
    give short photo queries a language cue; category: only results of one CATEGORIES entry (filter pill);
    spelling: spell-checker guesses per word (search/spelling.py), applied only when the index knows the guess."""
    corrected = correct(conn, query, spelling) if spelling else None
    query = corrected or query
    t0 = time.perf_counter()
    vector = vector_search(conn, embedder, query, CANDIDATES)
    t1 = time.perf_counter()
    keyword = keyword_search(conn, query, CANDIDATES)
    t2 = time.perf_counter()
    image = image_search(conn, embedder, query, CANDIDATES, lang, query_embedder)
    t3 = time.perf_counter()
    definitions = symbol_search(conn, query)
    t4 = time.perf_counter()
    code_like = looks_like_code(query)
    apps = app_search(conn, query, None if code_like else embedder)
    settings = [] if code_like else settings_search(conn, query, embedder)
    shortcuts = [] if code_like else shortcut_search(conn, query)
    answer = None if category else instant_answer(query)
    word_definition = None if category or code_like else define(query)
    present = _categories(vector, keyword, image, definitions)
    extras = [("apps", apps), ("settings", settings), ("shortcuts", shortcuts)]
    present = [name for name, found in extras if found] + present
    if category in ("apps", "settings", "shortcuts"):  # only that kind, with room for more of it
        apps = app_search(conn, query, embedder, limit=limit) if category == "apps" else []
        settings = settings_search(conn, query, embedder, limit=limit) if category == "settings" else []
        shortcuts = shortcut_search(conn, query, limit=limit) if category == "shortcuts" else []
        vector, keyword, image, definitions = [], [], [], []
    elif category in CATEGORIES:  # a filter pill: only that kind, with room for more of it
        kinds = CATEGORIES[category]
        vector = [h for h in vector if h.kind in kinds]
        keyword = [h for h in keyword if h.kind in kinds]
        image = [h for h in image if h.kind in kinds]
        definitions = definitions if category == "code" else []
        apps, settings, shortcuts = [], [], []
        limit = limit + limit // 2
    else:
        category = None
    # Photos (and the text read from them) only appear in the photos section, video frames too (with their moment).
    is_photo = ("image", "ocr")
    file_vector = [h for h in vector if h.kind not in is_photo]
    results = fuse(file_vector, [h for h in keyword if h.kind not in is_photo], limit)
    photos = photo_results(image, keyword, vector, query=query, limit=limit if category else PHOTO_SECTION_SIZE)
    models = 2 if query_embedder is not None else 1
    # Every word of the query found in the text of an image near the top of the word list: an exact answer.
    exact_text = any(h.kind == "ocr" and h.coverage == 1.0 for h in keyword[:3])
    photo_confidence = max(photo_list_confidence(image, models) if image else 0.0, 1.0 if exact_text else 0.0)
    photos_first = (
        bool(photos)
        and (exact_text or not looks_like_code(query))
        and photo_confidence > text_list_confidence(file_vector)
    )
    scores = _section_scores(query, results, file_vector, keyword, image, photos, exact_text, apps, settings,
                             shortcuts, definitions, answer, word_definition)  # fmt: skip
    if category == "photos" and photos:
        scores.setdefault("photos", 0.5)  # the Photos filter shows them all, however weak
    if "photos" not in scores:
        photos = []  # photos that only look a bit like the query: noise, not shown
    resp = SearchResponse(
        results, None, (t1 - t0) * 1000, (t2 - t1) * 1000, (t3 - t2) * 1000, photos=photos,
        photos_first=photos_first, definitions=definitions, symbol_ms=(t4 - t3) * 1000,
        categories=present, category=category, completion=completion(conn, query, category),
        apps=apps, apps_first=bool(apps) and apps[0].match != "meaning",
        settings=settings, settings_first=bool(settings) and settings[0].match != "meaning",
        settings_app_name=_settings_app_name(conn) if settings or "settings" in present else None,
        shortcuts=shortcuts, answer=answer, definition=word_definition,
        definition_first=bool(word_definition) and not any(r.keyword_rank for r in results),
        section_scores=scores, corrected_query=corrected,
    )  # fmt: skip
    if log:
        resp = replace(resp, event_id=log_search(conn, query, resp.sections()))
    return resp


def last_event(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT id, query, results_json FROM search_events ORDER BY id DESC LIMIT 1").fetchone()


def mark_opened(conn: sqlite3.Connection, event_id: int, chunk_id: int) -> None:
    conn.execute("UPDATE search_events SET opened_chunk_id = ? WHERE id = ?", (chunk_id, event_id))


def clear_history(conn: sqlite3.Connection) -> int:
    return conn.execute("DELETE FROM search_events").rowcount

"""Application results (the Applications section): names as text first, meaning only as a fallback.

Short and misspelt names are a text problem, not a meaning one: "dicsord" is one edit away from "Discord", while
its vector is close to nothing in particular. So names match by text (exact, prefix, word start, initials, one or
two typos), and the vector of the app's name + category + keywords only answers descriptions ("komunikator",
"photo editor") above a similarity floor.
"""

import json
import sqlite3
from dataclasses import dataclass

import numpy as np

from lantern.embedding.base import format_query
from lantern.store import db
from lantern.textnorm import fold

APP_SECTION_SIZE = 3
# Measured on 102 real apps (DECISIONS.md, "Applications"): good descriptions score 0.74-0.84, queries that should
# show no app at most 0.71. Meaning-only results are a hint below the other sections, never the top hit.
MEANING_FLOOR = 0.735
MEANING_BAND = 0.02  # keep the apps within this of the best one (several code editors fit "edytor kodu")


@dataclass(frozen=True)
class AppHit:
    path: str
    name: str
    category: str | None
    bundle_id: str | None
    match: str  # exact | prefix | word | initials | typo | keyword | meaning
    score: float


def _plain(text: str) -> str:
    return " ".join(fold(text).lower().split())


def _damerau(a: str, b: str, limit: int) -> int:
    """Optimal string alignment distance, giving up above `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def name_match(query: str, names: list[str], keywords: list[str]) -> tuple[str, float] | None:
    """How a typed query matches an app's names: the best level, or None."""
    q = _plain(query)
    if len(q) < 2:
        return None
    best: tuple[str, float] | None = None

    def offer(level: str, score: float) -> None:
        nonlocal best
        if best is None or score > best[1]:
            best = (level, score)

    for raw in names:
        name = _plain(raw)
        words = name.replace("-", " ").split()
        if q == name:
            offer("exact", 1.0)
        elif len(name) >= 3 and q.startswith(name + " "):  # "pogoda w warszawie", "chrome nowa karta"
            offer("leading", 0.65)
        elif len(name) >= 4 and " " not in name and name in q.split():  # "jaka pogoda", "otwórz discord"
            offer("contains", 0.62)
        elif name.startswith(q):
            offer("prefix", 0.9 - 0.1 * (1 - len(q) / len(name)))
        elif any(w.startswith(q) for w in words[1:]):
            offer("word", 0.75)
        elif len(q) >= 2 and len(words) >= 2 and "".join(w[0] for w in words).startswith(q):
            offer("initials", 0.7)
        elif len(q) >= 4:  # a typo while typing: compare with the start of the name of the same length
            limit = 1 if len(q) < 8 else 2
            if _damerau(q, name[: len(q)], limit) <= limit or _damerau(q, name, limit) <= limit:
                offer("typo", 0.6)
    if best is None and len(q) >= 3:
        for raw in keywords:
            if _plain(raw).startswith(q):
                offer("keyword", 0.55)
                break
    return best


@dataclass(frozen=True)
class ShortcutHit:
    id: str
    name: str
    match: str
    score: float


def shortcut_search(conn: sqlite3.Connection, query: str, limit: int = APP_SECTION_SIZE) -> list[ShortcutHit]:
    """The user's shortcuts (Shortcuts app) by name, like apps."""
    hits = []
    for sid, name in conn.execute("SELECT id, name FROM shortcuts"):
        m = name_match(query, [name], [])
        if m:
            hits.append(ShortcutHit(sid, name, m[0], m[1]))
    hits.sort(key=lambda h: (-h.score, len(h.name)))
    return hits[:limit]


def app_search(conn: sqlite3.Connection, query: str, embedder=None, limit: int = APP_SECTION_SIZE) -> list[AppHit]:
    rows = conn.execute(
        "SELECT path, name, names, keywords, category, bundle_id, model_id, embedding FROM apps"
    ).fetchall()
    if not rows or not query.strip():
        return []
    hits = []
    for r in rows:
        m = name_match(query, [r["name"], *json.loads(r["names"])], json.loads(r["keywords"]))
        if m:
            hits.append(AppHit(r["path"], r["name"], r["category"], r["bundle_id"], m[0], m[1]))
    if hits:
        hits.sort(key=lambda h: (-h.score, len(h.name), h.name))
        return hits[:limit]
    # No name matches: perhaps a description of what the app does.
    if embedder is None or len(query.split()) > 6:
        return []
    model_id = db.find_model(conn, embedder.model_info())
    rows = [r for r in rows if r["model_id"] == model_id]  # never compare vectors of different models
    if not rows:
        return []
    qv = embedder.embed_text([format_query(query)])[0].astype(np.float32)
    matrix = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    sims = matrix @ qv
    order = np.argsort(-sims)
    if sims[order[0]] < MEANING_FLOOR:
        return []
    top = [i for i in order[:limit] if sims[i] >= max(MEANING_FLOOR, sims[order[0]] - MEANING_BAND)]
    return [AppHit(rows[i]["path"], rows[i]["name"], rows[i]["category"], rows[i]["bundle_id"], "meaning",
                   float(sims[i])) for i in top]  # fmt: skip

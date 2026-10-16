"""System Settings results: pane names like app names, single settings by their words, meaning as a fallback.

A setting matches when every word of the query starts a word of its title, its other-language titles, Apple's
index words or its pane name ("24 godz" -> "Używaj formatu 24-godzinnego na pasku menu"). Descriptions without
those words ("ciemny motyw", "make text bigger") fall back to the vector of the setting, above a measured floor.
"""

import json
import re
import sqlite3
from dataclasses import dataclass

import numpy as np

from lantern.embedding.base import format_query
from lantern.search.apps import _plain, name_match
from lantern.store import db

SETTINGS_SECTION_SIZE = 4
# Measured on the ~600 panes and settings of macOS 26 (DECISIONS.md, "System Settings"): good descriptions 0.79-0.86,
# queries that should show no setting at most 0.67.
MEANING_FLOOR = 0.78
MEANING_BAND = 0.02
_WORD = re.compile(r"[\w]+", re.UNICODE)


@dataclass(frozen=True)
class SettingHit:
    key: str
    pane: str
    title: str
    url: str
    symbol: str | None
    color: str | None
    is_pane: bool
    match: str  # exact | prefix | word | initials | typo | words | meaning
    score: float


def _hit(r: sqlite3.Row, match: str, score: float) -> SettingHit:
    return SettingHit(r["key"], r["pane"], r["title"], r["url"], r["symbol"], r["color"], "/" not in r["key"], match,
                      score)  # fmt: skip


def _word_hit(word: str, token: str) -> bool:
    """A typed word matches the start of a setting's word; letters-only words under 4 letters only a whole word
    ("koń" is not the start of "kończenie", but "24" and "godz" still find "24-godzinnego")."""
    if len(token) < 4 and token.isalpha():
        return word == token
    return word.startswith(token)


def settings_search(conn: sqlite3.Connection, query: str, embedder=None,
                    limit: int = SETTINGS_SECTION_SIZE) -> list[SettingHit]:  # fmt: skip
    rows = conn.execute(
        "SELECT key, pane, title, names, keywords, url, symbol, color, model_id, embedding FROM system_settings"
    ).fetchall()
    tokens = [t for t in _WORD.findall(_plain(query)) if len(t) >= 2]
    if not rows or not tokens or (len(tokens) == 1 and len(tokens[0]) < 3):
        return []
    hits = []
    for r in rows:
        names = json.loads(r["names"])
        if "/" not in r["key"]:  # a pane: matched like an app name
            m = name_match(query, [r["title"], *names], [])
            if m:
                hits.append(_hit(r, m[0], m[1]))
            continue
        words = {w for text in [r["title"], r["pane"], *names, *json.loads(r["keywords"])]
                 for w in _WORD.findall(_plain(text))}  # fmt: skip
        if all(any(_word_hit(w, t) for w in words) for t in tokens):
            in_title = sum(any(_word_hit(w, t) for w in _WORD.findall(_plain(r["title"]))) for t in tokens)
            hits.append(_hit(r, "words", 0.5 + 0.2 * in_title / len(tokens)))
    if hits:
        hits.sort(key=lambda h: (-h.score, not h.is_pane, len(h.title)))
        return hits[:limit]
    if embedder is None or len(tokens) > 8:
        return []
    model_id = db.find_model(conn, embedder.model_info())
    rows = [r for r in rows if r["model_id"] == model_id]  # never compare vectors of different models
    if not rows:
        return []
    qv = embedder.embed_text([format_query(query)])[0].astype(np.float32)
    sims = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]) @ qv
    order = np.argsort(-sims)
    if sims[order[0]] < MEANING_FLOOR:
        return []
    return [_hit(rows[i], "meaning", float(sims[i])) for i in order[:limit]
            if sims[i] >= max(MEANING_FLOOR, sims[order[0]] - MEANING_BAND)]  # fmt: skip

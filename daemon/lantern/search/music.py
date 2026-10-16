"""Music results: the user's Apple Music songs and playlists (lantern/music.py) by their words, meaning as a fallback.

Every content word of the query has to match a word of the song (title, artist, album) at its start, either way
round, so Polish inflection works ("travisa scotta" finds Travis Scott, "skeletons" finds Skeletons). Words that only
name the kind ("piosenka", "song", "playlista") are not required; "playlista" alone lists the playlists. A song
opens in the context of a playlist that holds it, so the next songs follow.
"""

import json
import re
import sqlite3
from dataclasses import dataclass

import numpy as np

from lantern.embedding.base import format_query
from lantern.search.apps import MEANING_BAND, MEANING_FLOOR
from lantern.store import db
from lantern.textnorm import STOPWORDS, _plain

MUSIC_SECTION_SIZE = 4
_KIND_WORDS = {"piosenka", "piosenke", "piosenki", "piosenek", "utwor", "utwory", "utworu", "song", "songs", "track",
               "muzyka", "muzyke", "music", "album", "albumu", "plyta", "plyty"}  # fmt: skip
_PLAYLIST_WORDS = {"playlista", "playliste", "playlisty", "playlist", "playlists", "playlistę"}
_WORD = re.compile(r"\w+")


@dataclass(frozen=True)
class MusicHit:
    id: str
    kind: str  # song | playlist
    title: str
    artist: str
    album: str
    track: int
    downloaded: bool
    playlist_id: str | None  # a song: a playlist holding it (play in its context); a playlist: itself
    playlist_name: str | None
    count: int  # a playlist: its number of songs
    match: str  # title_exact | title | artist | album | featured | playlist | kind | meaning
    score: float


def _words(text: str) -> list[str]:
    return _WORD.findall(_plain(text))


def _fits(token: str, words: list[str]) -> bool:
    """A typed word fits a word of the song: equal, or one starts the other (4+ letters for the shorter part)."""
    for w in words:
        if token == w or (len(token) >= 3 and w.startswith(token)) or (len(w) >= 4 and token.startswith(w)):
            return True
    return False


def music_search(
    conn: sqlite3.Connection, query: str, embedder=None, limit: int = MUSIC_SECTION_SIZE
) -> list[MusicHit]:
    rows = conn.execute("SELECT id, kind, title, artist, album, track, downloaded, members, model_id, embedding "
                        "FROM music").fetchall()  # fmt: skip
    if not rows or not query.strip():
        return []
    names = {r["id"]: r["title"] for r in rows if r["kind"] == "playlist"}
    counts = {r["id"]: len(json.loads(r["members"])) for r in rows if r["kind"] == "playlist"}
    tokens = [t for t in _words(query) if t not in STOPWORDS]
    wants_playlist = any(t in _PLAYLIST_WORDS for t in tokens)
    content = [t for t in tokens if t not in _KIND_WORDS and t not in _PLAYLIST_WORDS]

    def hit(r, match: str, score: float) -> MusicHit:
        if r["kind"] == "playlist":
            return MusicHit(r["id"], "playlist", r["title"], "", "", 0, False, r["id"], r["title"],
                            counts.get(r["id"], 0), match, score)  # fmt: skip
        holders = [p for p in json.loads(r["members"]) if p in names]
        playlist = min(holders, key=lambda p: counts.get(p, 0)) if holders else None  # the most specific one
        return MusicHit(r["id"], "song", r["title"], r["artist"], r["album"], r["track"], bool(r["downloaded"]),
                        playlist, names.get(playlist) if playlist else None, 0, match, score)  # fmt: skip

    hits = []
    if not content and wants_playlist:  # "playlista": the user's playlists, biggest first
        hits = [hit(r, "kind", 0.8) for r in rows if r["kind"] == "playlist"]
        hits.sort(key=lambda h: -h.count)
        return hits[:limit]
    if not content:
        return []
    q = " ".join(content)
    for r in rows:
        if r["kind"] == "playlist":
            title = _words(r["title"])
            if title and all(_fits(t, title) for t in content):
                hits.append(hit(r, "playlist", 0.9 if " ".join(title) == q else 0.8))
            continue
        if wants_playlist:
            continue
        title, people, album = _words(r["title"]), _words(r["artist"]), _words(r["album"])
        if not all(_fits(t, title + people + album) for t in content):
            continue
        main = _words(re.sub(r"\s*[(\[].*?[)\]]", "", r["title"]))  # without "(feat. Travis Scott)", "[Remix]"
        if " ".join(main) == q:
            hits.append(hit(r, "title_exact", 0.95))
        elif all(_fits(t, main) for t in content):
            hits.append(hit(r, "title", 0.85))
        elif all(_fits(t, people) for t in content):
            hits.append(hit(r, "artist", 0.7))
        elif all(_fits(t, title) for t in content):  # only in the brackets: a guest ("feat. Travis Scott")
            hits.append(hit(r, "featured", 0.65))
        else:
            hits.append(hit(r, "album", 0.7))
    if hits:
        rank = {"title_exact": 0, "playlist": 1, "title": 2, "artist": 3, "album": 3, "featured": 4}
        hits.sort(key=lambda h: (rank.get(h.match, 4), h.album, h.track, h.title))
        return hits[:limit]
    # No words matched: perhaps a description ("smutna piosenka po angielsku" will not, "rap travisa" may).
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
    top = [i for i in order[:limit] if sims[i] >= max(MEANING_FLOOR, sims[order[0]] - MEANING_BAND)]
    return [hit(rows[i], "meaning", float(sims[i])) for i in top]

"""The user's music libraries: songs and playlists, sent by the app through `api_music`, one source at a time.

- apple: Apple Music, read on the Mac with Apple's iTunesLibrary framework (read-only).
- spotify: the user's Spotify playlists and liked songs, fetched by the app from Spotify's Web API with the user's own
  key, only when they switched it on (the one network connection Lantern makes, see docs/PRIVACY.md).

Only names are kept (title, artist, album, track number, playlist membership); nothing about the account. Searching is
local; playing goes through the Music or Spotify app."""

import json
import sqlite3
from dataclasses import dataclass, field

import numpy as np


@dataclass
class MusicItem:
    id: str
    kind: str  # song | playlist
    title: str
    artist: str = ""
    album: str = ""
    track: int = 0
    downloaded: bool = False
    members: list[str] = field(default_factory=list)  # song: playlist ids; playlist: song ids in order
    source: str = "apple"  # apple | spotify

    def document(self) -> str:
        """The text the vector is made of."""
        if self.kind == "playlist":
            return f"{self.title}. Playlist"
        return ". ".join(p for p in (self.title, self.artist, self.album, "song, music") if p)


def items_from(songs: list[dict], playlists: list[dict], source: str = "apple") -> list[MusicItem]:
    """Songs and playlists as the app sends them (MusicLibrary.swift); a song learns which playlists hold it."""
    holders: dict[str, list[str]] = {}
    lists = []
    for p in playlists:
        ids = [str(i) for i in p.get("songs") or []]
        lists.append(MusicItem(str(p["id"]), "playlist", str(p.get("name") or ""), members=ids, source=source))
        for song_id in dict.fromkeys(ids):
            holders.setdefault(song_id, []).append(str(p["id"]))
    tracks = [
        MusicItem(str(s["id"]), "song", str(s.get("title") or ""), str(s.get("artist") or ""),
                  str(s.get("album") or ""), int(s.get("track") or 0), bool(s.get("downloaded")),
                  holders.get(str(s["id"]), []), source)
        for s in songs
    ]  # fmt: skip
    return [i for i in tracks + lists if i.title]


def store_music(conn: sqlite3.Connection, items: list[MusicItem], embed, model_id: int, source: str = "apple") -> int:
    """Replace one source's rows; vectors only for new or changed documents (or another model). Returns the number
    embedded."""
    items = [i for i in items if i.source == source]
    old = {r["id"]: r for r in conn.execute("SELECT id, document, model_id, embedding FROM music WHERE source = ?",
                                            (source,))}  # fmt: skip
    todo = [i for i in items if (o := old.get(i.id)) is None or o["model_id"] != model_id
            or o["document"] != i.document()]  # fmt: skip
    vectors = embed([i.document() for i in todo]) if todo else []
    fresh = {i.id: np.asarray(v, dtype=np.float32).tobytes() for i, v in zip(todo, vectors, strict=True)}
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM music WHERE source = ?", (source,))
        conn.executemany(
            "INSERT INTO music(id, kind, source, title, artist, album, track, downloaded, members, document, "
            "model_id, embedding) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (i.id, i.kind, source, i.title, i.artist, i.album, i.track, int(i.downloaded),
                 json.dumps(i.members), i.document(), model_id, fresh.get(i.id) or old[i.id]["embedding"])
                for i in items
            ],
        )  # fmt: skip
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return len(todo)

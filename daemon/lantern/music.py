"""The user's Apple Music library: songs and playlists, as the app reads them with Apple's iTunesLibrary framework
(read-only, on the Mac) and sends them through `api_music`. Only names are kept (title, artist, album, track number,
playlist membership); nothing about the account. Playing goes through the Music app, in the app."""

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

    def document(self) -> str:
        """The text the vector is made of."""
        if self.kind == "playlist":
            return f"{self.title}. Playlist"
        return ". ".join(p for p in (self.title, self.artist, self.album, "song, music") if p)


def items_from(songs: list[dict], playlists: list[dict]) -> list[MusicItem]:
    """Songs and playlists as the app sends them (MusicLibrary.swift); a song learns which playlists hold it."""
    holders: dict[str, list[str]] = {}
    lists = []
    for p in playlists:
        ids = [str(i) for i in p.get("songs") or []]
        lists.append(MusicItem(str(p["id"]), "playlist", str(p.get("name") or ""), members=ids))
        for song_id in dict.fromkeys(ids):
            holders.setdefault(song_id, []).append(str(p["id"]))
    tracks = [
        MusicItem(str(s["id"]), "song", str(s.get("title") or ""), str(s.get("artist") or ""),
                  str(s.get("album") or ""), int(s.get("track") or 0), bool(s.get("downloaded")),
                  holders.get(str(s["id"]), []))
        for s in songs
    ]  # fmt: skip
    return [i for i in tracks + lists if i.title]


def store_music(conn: sqlite3.Connection, items: list[MusicItem], embed, model_id: int) -> int:
    """Replace the table; vectors only for new or changed documents (or another model). Returns the number embedded."""
    old = {r["id"]: r for r in conn.execute("SELECT id, document, model_id, embedding FROM music")}
    todo = [i for i in items if (o := old.get(i.id)) is None or o["model_id"] != model_id
            or o["document"] != i.document()]  # fmt: skip
    vectors = embed([i.document() for i in todo]) if todo else []
    fresh = {i.id: np.asarray(v, dtype=np.float32).tobytes() for i, v in zip(todo, vectors, strict=True)}
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM music")
        conn.executemany(
            "INSERT INTO music(id, kind, title, artist, album, track, downloaded, members, document, model_id, "
            "embedding) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (i.id, i.kind, i.title, i.artist, i.album, i.track, int(i.downloaded), json.dumps(i.members),
                 i.document(), model_id, fresh.get(i.id) or old[i.id]["embedding"])
                for i in items
            ],
        )  # fmt: skip
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return len(todo)

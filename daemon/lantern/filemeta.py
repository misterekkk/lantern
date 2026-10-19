"""Fill the time and place of indexed files (extractors/metadata.py) in small batches: new files and files indexed
before this existed, without re-indexing anything. The daemon calls it from its control loop, `lantern index` after
indexing. Reads file headers only; never a file whose content is only in iCloud.
"""

import sqlite3
from collections.abc import Callable
from pathlib import Path

from lantern import places as places_mod
from lantern.extractors import metadata
from lantern.store import db

META_VERSION = 1  # bump when the cascade changes: every file is read again (no re-embedding)
BATCH = 200


def refresh(
    conn: sqlite3.Connection, places: bool = True, limit: int = BATCH, should_stop: Callable[[], bool] = lambda: False
) -> int:
    """Read up to `limit` files whose metadata is missing or older than META_VERSION. Returns how many were read."""
    rows = conn.execute(
        "SELECT id, path, kind FROM files WHERE meta_version < ? ORDER BY id LIMIT ?", (META_VERSION, limit)
    ).fetchall()
    if not rows:
        return 0
    updates = []
    for row in rows:
        if should_stop():
            break
        path = Path(row["path"])
        meta = metadata.read(path, row["kind"], places=places)
        place = places_mod.nearest(meta.lat, meta.lon) if places and meta.lat is not None else None
        country = None
        if places and place is None and row["kind"] in ("image", "video") and meta.source in ("exif", "quicktime"):
            # a camera without GPS: the folder may name the country ("Portugalia/IMG_6794.JPG"); only for real
            # photos and videos, never screenshots or graphics, whose folders name other things
            country = places_mod.country_in_path(path)
        updates.append([meta.date, meta.source, meta.lat, meta.lon, place, country, META_VERSION, row["id"]])
    new_places = {u[4] for u in updates if u[4] is not None}
    with db.transaction(conn):
        if new_places:
            marks = ",".join("?" * len(new_places))
            known = {r[0] for r in conn.execute(f"SELECT id FROM places WHERE id IN ({marks})", list(new_places))}
            if new_places - known:
                places_mod.store(conn, places_mod.load(new_places - known))
            countries = dict(conn.execute(f"SELECT id, country FROM places WHERE id IN ({marks})", list(new_places)))
            for u in updates:
                if u[4] is not None:
                    u[5] = countries.get(u[4])  # a city's country
        conn.executemany(
            "UPDATE files SET inferred_date = ?, date_source = ?, lat = ?, lon = ?, place_id = ?, country = ?, "
            "meta_version = ? WHERE id = ?",
            updates,
        )
    return len(updates)


def pending(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT count(*) FROM files WHERE meta_version < ?", (META_VERSION,)).fetchone()[0]


def forget_places(conn: sqlite3.Connection) -> None:
    """The "places" setting switched off: coordinates and places disappear from the index (deleted data must really
    disappear); dates stay."""
    with db.transaction(conn):
        conn.execute("UPDATE files SET lat = NULL, lon = NULL, place_id = NULL, country = NULL WHERE lat IS NOT NULL "
                     "OR place_id IS NOT NULL OR country IS NOT NULL")  # fmt: skip
        places_mod.drop_unused(conn)


def read_places_again(conn: sqlite3.Connection) -> None:
    """The setting switched on: photos and videos are read again for their coordinates (and folder countries)."""
    conn.execute("UPDATE files SET meta_version = 0 WHERE kind IN ('image', 'video', 'audio')")

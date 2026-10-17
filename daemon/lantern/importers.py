"""What other launchers learned, brought in as learned choices so a new user starts where they left off.

Alfred keeps its knowledge in `~/Library/Application Support/Alfred/Databases/knowledge.alfdb` (SQLite, schema seen
on Alfred 5.8): `knowledge(item, keyword, ts, hidden)` is every choice (what was typed, what was opened),
`latching(item, keyword, strong, ts)` the keywords it tied to one result. The item of an app is taken as its path or its
bundle id (both handled; the exact form could not be verified without driving Alfred's window). Only apps are
imported: their row ids are stable; files would need Lantern's own chunk ids. Raycast's database is encrypted (key in
the keychain), so it is left alone. Read-only: Alfred's file is never changed.
"""

import sqlite3
from pathlib import Path

from lantern.store import db

ALFRED_DB = Path.home() / "Library/Application Support/Alfred/Databases/knowledge.alfdb"
MARK = '[{"imported": "alfred"}]'  # results_json of imported choices: told apart from Lantern's own searches
COCOA_EPOCH = 978307200  # 2001-01-01 in Unix time


def _app_key(item: str, by_bundle: dict[str, str]) -> str | None:
    item = (item or "").strip()
    path = item if item.endswith(".app") else by_bundle.get(item)
    return f"app|0|-1|{path}|" if path and Path(path).exists() else None


def _unix(ts) -> int:
    try:
        value = float(ts)
    except (TypeError, ValueError):
        return db.now()
    return int(value + COCOA_EPOCH if value < 1_000_000_000 else value)


def import_alfred(conn: sqlite3.Connection, path: Path = ALFRED_DB) -> int:
    """Adds Alfred's app choices to the search history once (a second import skips them). Returns the count."""
    if not path.is_file():
        return 0
    source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        choices = source.execute("SELECT item, keyword, ts FROM knowledge WHERE COALESCE(hidden, 0) = 0").fetchall()
        # a latched keyword counts as two choices: enough for Lantern's rule (twice, most of the choices)
        latched = source.execute("SELECT item, keyword, ts FROM latching").fetchall()
    except sqlite3.DatabaseError:
        return 0
    finally:
        source.close()
    by_bundle = {r[0]: r[1] for r in conn.execute("SELECT bundle_id, path FROM apps WHERE bundle_id IS NOT NULL")}
    rows = []
    for item, keyword, ts in [*choices, *latched, *latched]:
        key = _app_key(item, by_bundle)
        query = (keyword or "").strip()
        if key and query:
            rows.append((_unix(ts), query, key))
    added = 0
    with db.transaction(conn):
        for ts, query, key in rows:
            exists = conn.execute("SELECT COUNT(*) FROM search_events WHERE results_json = ? AND query = ? AND "
                                  "opened_key = ? AND ts = ?", (MARK, query, key, ts)).fetchone()[0]  # fmt: skip
            if exists >= sum(1 for r in rows if r == (ts, query, key)):
                continue
            conn.execute("INSERT INTO search_events(ts, query, results_json, opened_chunk_id, opened_key) "
                         "VALUES (?, ?, ?, 0, ?)", (ts, query, MARK, key))  # fmt: skip
            added += 1
    return added

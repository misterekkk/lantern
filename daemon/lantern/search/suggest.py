"""Inline completion for the query field (accepted with → in the panel), like Spotlight completing a name.

Lantern searches by meaning, so it never guesses the rest of a sentence. It completes what the user has already used
or what exists by name: earlier queries from the local history, app and System Settings names, then (for one word)
a code definition or a file name.
Everything is read from the index; nothing leaves the Mac.
"""

import json
import sqlite3

from lantern.chunking.symbols import name_key

MIN_PREFIX = 2


def _like_prefix(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _starts(candidate: str, typed: str) -> bool:
    return len(candidate) > len(typed) and candidate.casefold().startswith(typed.casefold())


def completion(conn: sqlite3.Connection, typed: str) -> str | None:
    """The typed text plus a suggested ending (keeping what the user typed), or None."""
    if len(typed.strip()) < MIN_PREFIX or typed != typed.lstrip():
        return None
    # 1. Earlier queries that led to an opened result, most recent first: the user's own wording (searches made
    #    while typing, never followed by an open, are not suggestions).
    for (query,) in conn.execute(
        "SELECT query FROM search_events WHERE opened_chunk_id IS NOT NULL AND query LIKE ? ESCAPE '\\' "
        "ORDER BY id DESC LIMIT 50",
        (_like_prefix(typed),),
    ):
        if _starts(query, typed):
            return typed + query[len(typed) :]
    # 2. An application or a System Settings pane by any of its names (what Spotlight completes: the top hit).
    names = []
    for table, columns in (("apps", "name, names"), ("system_settings", "title, names")):
        query = f"SELECT {columns} FROM {table}" + (" WHERE key NOT LIKE '%/%'" if table == "system_settings" else "")
        for first, others in conn.execute(query):
            names += [first, *json.loads(others)]
    names = [n for n in names if _starts(n, typed)]
    if names:
        best = min(names, key=len)
        return typed + best[len(typed) :]
    if " " in typed.strip():
        return None
    # 3. A code definition (the shortest name that starts with the typed text).
    key = name_key(typed)
    if key:
        rows = conn.execute("SELECT name FROM symbols WHERE name_key >= ? AND name_key < ? LIMIT 200",
                            (key, key + "\U0010ffff"))  # fmt: skip
        names = [r[0] for r in rows]
        names = [n for n in names if _starts(n, typed)]
        if names:
            best = min(names, key=len)
            return typed + best[len(typed) :]
    # 4. A file name.
    if len(typed) >= 3:
        rows = conn.execute(
            "SELECT path FROM files WHERE path LIKE ? ESCAPE '\\' LIMIT 200", ("%/" + _like_prefix(typed),)
        )
        names = [r[0].rsplit("/", 1)[-1] for r in rows]
        names = [n for n in names if _starts(n, typed)]
        if names:
            best = min(names, key=len)
            return typed + best[len(typed) :]
    return None

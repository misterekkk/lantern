"""Scoped search: `in:<folder>` in a query keeps results to files under folders whose name starts with that word
("in:down umowa", "in:Studia", "in:\"Moje dokumenty\""), or under a path ("in:~/Projects/lantern"). The token is cut
from the query before anything else (spelling, embedding), so the model never sees it; the scope becomes a SQL
condition on the file path, not a filter applied after ranking.
"""

import os
import re
import sqlite3

_SCOPE = re.compile(r'(?:^|\s)in:(?:"([^"]+)"|(\S+))', re.IGNORECASE)
EXACT_SCOPE_MAX = 20000  # chunks: up to this many, vectors in the scope are scored exactly instead of by KNN


def split_scope(query: str) -> tuple[str, str | None]:
    """(query without the first `in:` token, LIKE pattern for files.path or None)."""
    m = _SCOPE.search(query)
    if not m:
        return query, None
    token = (m.group(1) or m.group(2) or "").strip().rstrip("/")
    rest = " ".join((query[: m.start()] + " " + query[m.end() :]).split())
    if not token:
        return rest, None
    return rest, like_pattern(token)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_pattern(token: str) -> str:
    """A path ("~/x", "/x") scopes to everything under it; a word to every folder whose name starts with it."""
    if token.startswith(("~", "/")):
        return _escape(os.path.expanduser(token).rstrip("/")) + "/%"
    return "%/" + _escape(token) + "%/%"


PATH_CONDITION = "f.path LIKE ? ESCAPE '\\'"


def chunk_ids(conn: sqlite3.Connection, pattern: str, kinds: tuple[str, ...] | None = None) -> list[int]:
    sql = f"SELECT c.id FROM chunks c JOIN files f ON f.id = c.file_id WHERE {PATH_CONDITION}"
    params: list = [pattern]
    if kinds:
        sql += f" AND c.kind IN ({','.join('?' * len(kinds))})"
        params += list(kinds)
    return [r[0] for r in conn.execute(sql, params)]

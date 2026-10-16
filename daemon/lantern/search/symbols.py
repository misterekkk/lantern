"""Definition search: "def add_two(x, y):" -> skrypt.py line 200. Reads the symbols table (chunking/symbols.py).

The query is parsed for an identifier, a kind hint (def/class/...), an optional parent (Foo::bar, A.m) and the number
of arguments. Names match style-insensitively (add_two = addTwo): exact, then prefix, then fragment (parseUserToken
for "UserToken"). Natural-language queries only get exact matches of a single word, so "pies" never lists
definitions that merely contain those letters.
"""

import re
import sqlite3
from dataclasses import dataclass

from lantern.chunking.symbols import arity, name_key

DEFINITION_SECTION_SIZE = 5
_IDENT = re.compile(r"[A-Za-z_$][\w$]*(?:(?:::|\.|->)[A-Za-z_$][\w$]*)*")
# Identifiers, calls, paths: "def add_two(x, y):", "parseUserToken", "config.toml".
_CODE_LIKE = re.compile(r"[_(){}\[\];:=<>/\\]|\w\.\w|[a-z][A-Z]")


def looks_like_code(query: str) -> bool:
    return bool(_CODE_LIKE.search(query))


_KIND_HINTS = {
    "def": "function", "function": "function", "func": "function", "fn": "function", "fun": "function",
    "sub": "function", "method": "function", "procedure": "function", "defp": "function",
    "class": "class", "struct": "class", "object": "class", "record": "class", "impl": "class",
    "interface": "interface", "protocol": "interface", "trait": "interface",
    "enum": "enum", "type": "type", "module": "module", "namespace": "module", "mod": "module",
    "table": "table", "view": "table",
}  # fmt: skip
_HINT_KINDS = {
    "function": {"function", "method"},
    "class": {"class", "struct"},
    "interface": {"interface", "trait"},
    "enum": {"enum"},
    "type": {"type", "struct", "class", "interface", "enum"},
    "module": {"module"},
    "table": {"table"},
}
# Words around a name in a declaration that are never the name itself.
_NOISE = frozenset(
    {
        "async", "static", "public", "private", "protected", "internal", "export", "default", "const", "let", "var",
        "val", "void", "int", "long", "float", "double", "bool", "boolean", "char", "string", "str", "auto", "final",
        "abstract", "override", "virtual", "inline", "extern", "unsafe", "pub", "return", "new", "self", "this",
        "create", "or", "replace", "extends", "implements", "where", "local", "open", "data", "suspend", "operator",
    }
)  # fmt: skip


@dataclass(frozen=True)
class SymbolQuery:
    name: str
    key: str
    kind: str | None  # hint from a keyword: function | class | interface | enum | type | module | table
    parent: str | None  # Foo in Foo::bar / Foo.bar
    arity: int | None
    exact_only: bool  # plain single word: no prefix/fragment matches


@dataclass(frozen=True)
class Definition:
    path: str
    line: int
    end_line: int
    name: str
    kind: str
    signature: str
    parent: str | None
    match: str  # exact | prefix | partial
    score: float
    chunk_id: int | None  # chunk that contains the definition (for history / "opened")


def _depth0_identifiers(query: str) -> list[tuple[str, int]]:
    """Identifiers outside parentheses with their end offset (parameters are not candidates for the name)."""
    depth, out = 0, []
    pos = 0
    while pos < len(query):
        ch = query[pos]
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth = max(0, depth - 1)
        elif depth == 0:
            m = _IDENT.match(query, pos)
            if m and (pos == 0 or not (query[pos - 1].isalnum() or query[pos - 1] in "_$")):
                out.append((m.group(), m.end()))
                pos = m.end()
                continue
        pos += 1
    return out


def parse_symbol_query(query: str) -> SymbolQuery | None:
    query = query.strip()
    idents = _depth0_identifiers(query)
    kind = next((_KIND_HINTS[w.lower()] for w, _ in idents if w.lower() in _KIND_HINTS), None)
    names = [(w, end) for w, end in idents if w.lower() not in _KIND_HINTS and w.lower() not in _NOISE]
    if not names:
        return None
    code_like = looks_like_code(query) or kind is not None
    if not code_like and len(query.split()) > 1:  # natural language ("optymalizacja drzewa")
        return None
    # The name is the identifier right before "(" if there is one, otherwise the last one.
    called = [w for w, end in names if query[end:].lstrip().startswith("(")]
    full = called[0] if called else names[-1][0]
    parts = re.split(r"::|\.|->", full)
    name, parent = parts[-1], (parts[-2] if len(parts) > 1 else None)
    key = name_key(name)
    if not key:
        return None
    return SymbolQuery(name, key, kind, parent, arity(query), exact_only=not code_like)


def _chunk_at(conn: sqlite3.Connection, file_id: int, line: int) -> int | None:
    row = conn.execute(
        "SELECT id FROM chunks WHERE file_id = ? AND start_line <= ? AND end_line >= ? "
        "ORDER BY end_line - start_line LIMIT 1",
        (file_id, line, line),
    ).fetchone()
    return row[0] if row else None


_WORDS = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def fragment_at_word_start(name: str, key: str) -> bool:
    """True when `key` (a style-insensitive name key) starts at a word of `name`: "UserToken" in parseUserToken or
    parse_user_token, but "enter" is not in center_only_red (it starts inside "center")."""
    words = _WORDS.findall(name.rsplit("::", 1)[-1].rsplit(".", 1)[-1])
    joined, starts, at = "", set(), 0
    for word in words:
        starts.add(at)
        joined += word.lower()
        at += len(word)
    return any(joined.startswith(key, i) for i in starts)


def symbol_search(conn: sqlite3.Connection, query: str, limit: int = DEFINITION_SECTION_SIZE) -> list[Definition]:
    q = parse_symbol_query(query)
    if q is None:
        return []
    # (match, condition, parameters, base score). Prefix as a range so the name_key index is used (LIKE is
    # case-insensitive in SQLite and skips it); a fragment needs a scan (~14 ms at 300k symbols).
    levels = [("exact", "s.name_key = ?", (q.key,), 3.0)]
    if not q.exact_only and len(q.key) >= 3:
        levels.append(("prefix", "s.name_key > ? AND s.name_key < ?", (q.key, q.key + "\U0010ffff"), 2.0))
        if len(q.key) >= 4:
            levels.append(("partial", "instr(s.name_key, ?) > 1", (q.key,), 1.0))
    found: dict[int, tuple[float, str, sqlite3.Row]] = {}  # symbol id -> (score, match, row)
    for match, where, params, base in levels:
        rows = conn.execute(
            "SELECT s.id, s.file_id, s.name, s.kind, s.parent, s.signature, s.line, s.end_line, f.path "
            f"FROM symbols s JOIN files f ON f.id = s.file_id WHERE {where} LIMIT 200",
            params,
        ).fetchall()
        for r in rows:
            if match == "partial" and not fragment_at_word_start(r["name"], q.key):
                continue
            score = base
            if q.kind and r["kind"] in _HINT_KINDS.get(q.kind, ()):
                score += 0.5
            if q.arity is not None and arity(r["signature"]) == q.arity:
                score += 0.5
            if q.parent and r["parent"] and name_key(r["parent"]) == name_key(q.parent):
                score += 0.5
            if r["name"] == q.name:  # same spelling, not just the same style-insensitive key
                score += 0.25
            found[r["id"]] = (score, match, r)
        if len(found) >= limit:
            break  # enough stronger matches: skip the weaker levels
    best = sorted(found.values(), key=lambda v: (-v[0], v[2]["path"], v[2]["line"]))[:limit]
    return [
        Definition(
            path=r["path"],
            line=r["line"],
            end_line=r["end_line"],
            name=r["name"],
            kind=r["kind"],
            signature=r["signature"],
            parent=r["parent"],
            match=match,
            score=score,
            chunk_id=_chunk_at(conn, r["file_id"], r["line"]),
        )  # fmt: skip
        for score, match, r in best
    ]

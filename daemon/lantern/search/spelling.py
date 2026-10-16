"""Spelling corrections chosen by the index, never by guesswork alone.

The app asks the macOS spell checker (NSSpellChecker, on the Mac) for words it does not know and sends its guesses
for each. Its own first choice is often wrong ("warszwie" -> "warstwie", "faktrua" -> "daktrua"), while the right word
is usually among the guesses. Lantern keeps a word the index knows (it is a name, a term, a code word in the user's
files) and replaces an unknown one only with the first guess the index knows: file text, app names, System Settings,
shortcuts. No known guess, no correction.
"""

import json
import re
import sqlite3

from lantern.textnorm import _plain, query_terms

_WORD = re.compile(r"^[^\W\d_]{4,40}$")


def _names_vocabulary(conn: sqlite3.Connection) -> set[str]:
    words: set[str] = set()

    def add(*texts: str) -> None:
        for text in texts:
            words.update(re.findall(r"\w+", _plain(text)))

    for r in conn.execute("SELECT name, names, keywords FROM apps"):
        add(r["name"], *json.loads(r["names"]), *json.loads(r["keywords"]))
    for r in conn.execute("SELECT pane, title, names, keywords FROM system_settings"):
        add(r["pane"], r["title"], *json.loads(r["names"]), *json.loads(r["keywords"]))
    for (name,) in conn.execute("SELECT name FROM shortcuts"):
        add(name)
    return words


class _Vocabulary:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._names: set[str] | None = None

    def knows(self, word: str) -> bool:
        terms = query_terms(word)
        if len(terms) != 1:
            return True  # a stopword or several parts: not a word to correct
        term, prefix = terms[0]
        try:
            hit = self.conn.execute(
                "SELECT 1 FROM chunks_fts WHERE chunks_fts MATCH ? LIMIT 1", (f'"{term}"*' if prefix else f'"{term}"',)
            ).fetchone()
        except sqlite3.OperationalError:
            hit = None
        if hit:
            return True
        if self._names is None:
            self._names = _names_vocabulary(self.conn)
        stem = _plain(term)
        return any(w.startswith(stem) for w in self._names) if prefix else stem in self._names


def correct(conn: sqlite3.Connection, query: str, guesses: dict[str, list[str]]) -> str | None:
    """The query with unknown words replaced by known guesses, or None when nothing changes."""
    vocabulary = _Vocabulary(conn)
    corrected = query
    for word, options in guesses.items():
        if not _WORD.match(word) or not re.search(rf"\b{re.escape(word)}\b", corrected) or vocabulary.knows(word):
            continue
        for option in options:
            if not _WORD.match(option) or _plain(option) == _plain(word):
                continue  # several words, or only accents ("prad" -> "prąd": search ignores them anyway)
            if vocabulary.knows(option):
                corrected = re.sub(rf"\b{re.escape(word)}\b", option, corrected, count=1)
                break
    return corrected if corrected != query else None

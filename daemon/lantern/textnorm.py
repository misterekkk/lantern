"""Text normalisation for keyword search (FTS5).

FTS5's `remove_diacritics 2` folds most Polish letters but not "ł" (it is a separate letter in Unicode, not a
base letter plus a combining mark), so "lodz" would not match "łódź". Fold it ourselves, at index and query time.
"""

import re

_FOLD = str.maketrans({"ł": "l", "Ł": "L"})
_TOKEN = re.compile(r"\w+", re.UNICODE)


def fold(text: str) -> str:
    return text.translate(_FOLD)


def fts_query(query: str) -> str | None:
    """Turn free text into an FTS5 OR-query of quoted tokens (no FTS syntax injection)."""
    tokens = [t for t in _TOKEN.findall(fold(query).lower()) if len(t) > 1]
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in dict.fromkeys(tokens))

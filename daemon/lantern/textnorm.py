"""Text normalisation and query building for keyword search (FTS5).

- FTS5's `remove_diacritics 2` folds most Polish letters but not "ł" (a separate letter in Unicode, not a base letter
  plus a combining mark), so "lodz" would not match "łódź". We fold it ourselves, at index and query time.
- Stopwords are dropped from queries ("jak połączyć się z bazą" -> "połączyć", "bazą").
- Polish inflection: longer words match by stem prefix ("decyzyjnego" -> decyzyjn*), a cheap stand-in for a stemmer.
"""

import re
import unicodedata

_FOLD = str.maketrans({"ł": "l", "Ł": "L"})
_TOKEN = re.compile(r"\w+", re.UNICODE)

# Folded (ł -> l), lowercase.
STOPWORDS = frozenset(
    """
    a aby albo ale ani az bardzo bez bo by byc byl byla bylo byly bym chce co czy czyli dla do dlaczego gdy gdzie go
    i ich ile im inne jak jaka jaki jakie jako je jego jej jest jestem jeszcze jesli juz ktora ktore ktory kiedy
    lub ma mam mi mnie moze my na nad nam nas nie niech nim nie o od oraz po pod przez przy sa sie sobie ta tak
    tam te tego tej ten to tu tym u w we wiec wszystko z za ze że się są być był była było już który która które
    jeśli może więc chcę
    an and are as at be but by can do does for from how i if in into is it its me my of on or our so that the
    their them then there these this to was we what when where which who why will with you your
    """.split()  # noqa: SIM905 - a word list reads better than 150 quoted strings
)


def fold(text: str) -> str:
    return text.translate(_FOLD)


def query_terms(query: str) -> list[tuple[str, bool]]:
    """Content terms of a query as (term, is_prefix): stopwords removed (unless that leaves nothing), long words
    reduced to a stem prefix; identifiers (snake_case), numbers and short words stay exact."""
    tokens = [t for t in _TOKEN.findall(fold(query).lower()) if len(t) > 1]
    content = [t for t in tokens if t not in STOPWORDS] or tokens
    terms = []
    for token in dict.fromkeys(content):
        if token.isalpha() and len(token) >= 5:
            keep = len(token) - (3 if len(token) >= 7 else 2)
            terms.append((token[: max(4, keep)], True))
        else:
            terms.append((token, False))
    return terms


def fts_query(query: str) -> str | None:
    """Free text -> FTS5 OR-query of quoted terms (no FTS syntax injection)."""
    terms = query_terms(query)
    if not terms:
        return None
    return " OR ".join(f'"{t}"*' if prefix else f'"{t}"' for t, prefix in terms)


def _plain(text: str) -> str:
    """Lowercase, ł folded, diacritics removed (what FTS5 compares)."""
    decomposed = unicodedata.normalize("NFKD", fold(text).lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def term_coverage(query: str, text: str) -> float:
    """Share of the query's content terms present in text (prefix terms match word starts)."""
    terms = query_terms(query)
    if not terms:
        return 0.0
    words = set(_TOKEN.findall(_plain(text)))
    hit = 0
    for term, prefix in terms:
        term = _plain(term)
        if (prefix and any(w.startswith(term) for w in words)) or (not prefix and term in words):
            hit += 1
    return hit / len(terms)

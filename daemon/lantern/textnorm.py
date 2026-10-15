"""Text normalisation and query building for keyword search (FTS5).

- FTS5's `remove_diacritics 2` folds most Polish letters but not "ł" (a separate letter in Unicode, not a base letter
  plus a combining mark), so "lodz" would not match "łódź". We fold it ourselves, at index and query time.
- Stopwords are dropped from queries ("jak połączyć się z bazą" -> "połączyć", "bazą").
- Polish inflection: longer words match by stem prefix ("decyzyjnego" -> decyzyjn*), a cheap stand-in for a stemmer.
"""

import re

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


def _term(token: str) -> str:
    if token.isalpha() and len(token) >= 5:
        keep = len(token) - (3 if len(token) >= 7 else 2)
        return f'"{token[: max(4, keep)]}"*'
    return f'"{token}"'  # identifiers (snake_case), numbers and short words: exact


def fts_query(query: str) -> str | None:
    """Free text -> FTS5 OR-query of quoted terms (no FTS syntax injection), stopwords removed, stems as prefixes."""
    tokens = [t for t in _TOKEN.findall(fold(query).lower()) if len(t) > 1]
    content = [t for t in tokens if t not in STOPWORDS] or tokens  # a stopword-only query still searches
    if not content:
        return None
    return " OR ".join(_term(t) for t in dict.fromkeys(content))

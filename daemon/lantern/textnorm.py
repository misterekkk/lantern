"""Text normalisation and query building for keyword search (FTS5).

- FTS5's `remove_diacritics 2` folds letters that decompose into a base letter plus a combining mark (ą, ü, ř, ș), but
  not letters that are separate characters in Unicode: "ł" (so "lodz" would not match "łódź"), ø, đ, ħ, ß, æ, þ and
  others (checked one by one, see tests/test_queue_and_store.py). We fold those ourselves, at index and query time,
  after NFKC (ligatures like "ﬁ" become "fi"). The Swift app mirrors the table (LanternCore/TextFold.swift).
- Stopwords are dropped from queries ("jak połączyć się z bazą" -> "połączyć", "bazą").
- Polish inflection: longer words match by stem prefix ("decyzyjnego" -> decyzyjn*), a cheap stand-in for a stemmer;
  stems keep at least 5 letters for words of 6+, so they stay specific.
"""

import re
import unicodedata

# Letters that NFKD + FTS5 leave alone (stroke letters, ligature letters, thorn, eth, eng, dotless i, sharp s).
_FOLD = str.maketrans(
    {
        "ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ð": "d", "Ð": "D", "ħ": "h", "Ħ": "H",
        "ŧ": "t", "Ŧ": "T", "ŋ": "n", "Ŋ": "N", "ı": "i", "ĸ": "k", "ƀ": "b", "Ƀ": "B", "ǥ": "g", "Ǥ": "G",
        "ƚ": "l", "Ƚ": "L", "ɨ": "i", "Ɨ": "I", "ʉ": "u", "Ʉ": "U", "ß": "ss", "ẞ": "SS", "æ": "ae", "Æ": "AE",
        "œ": "oe", "Œ": "OE", "þ": "th", "Þ": "TH",
    }
)  # fmt: skip
_TOKEN = re.compile(r"\w+", re.UNICODE)
# Numbers and codes with separators ("187,15", "FV/2025/09/0932", "2025-10-14", "config.toml"): FTS splits them at the
# punctuation, so they are searched as phrases (the parts next to each other), not as loose words.
_COMPOUND = re.compile(r"\w+(?:[.,/:\-]\w+)+", re.UNICODE)

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
    """Spell letters FTS5 cannot fold as their plain Latin equivalent ("Łódź" -> "Lódź", "Straße" -> "Strasse")."""
    if text.isascii():
        return text
    return unicodedata.normalize("NFKC", text).translate(_FOLD)


def query_terms(query: str) -> list[tuple[str, bool]]:
    """Content terms of a query as (term, is_prefix): stopwords removed (unless that leaves nothing), long words
    reduced to a stem prefix; identifiers (snake_case), numbers and short words stay exact. A compound with separators
    becomes one phrase term: its parts joined by spaces ("187 15")."""
    text = fold(query).lower()
    phrases = [" ".join(_TOKEN.findall(m.group())) for m in _COMPOUND.finditer(text)]
    text = _COMPOUND.sub(" ", text)
    tokens = [t for t in _TOKEN.findall(text) if len(t) > 1]
    content = [t for t in tokens if t not in STOPWORDS] or ([] if phrases else tokens)
    terms = [(phrase, False) for phrase in dict.fromkeys(phrases)]
    for token in dict.fromkeys(content):
        if token.isalpha() and len(token) >= 5:
            # Drop the likely ending, keeping a stem long enough to stay specific: "przepis" -> przep* (not prze*,
            # which also matched przez, przepływ…), "drzewa" -> drzew* (not drze*: drzwi), "kolejka" -> kolej*
            # (so "kolejce" still matches), "decyzyjnego" -> decyzyjn*.
            n = len(token)
            keep = n - 1 if n <= 6 else n - 2 if n <= 9 else n - 3
            terms.append((token[: max(keep, 5 if n >= 6 else 4)], True))
        else:
            terms.append((token, False))
    return terms


def fts_query(query: str) -> str | None:
    """Free text -> FTS5 OR-query of quoted terms (no FTS syntax injection)."""
    terms = query_terms(query)
    if not terms:
        return None
    parts = []
    for term, prefix in terms:
        parts.append(f'"{term}"*' if prefix else f'"{term}"')
        if " " in term:  # a phrase also retrieves its loose parts (recall); coverage still needs them adjacent
            parts += [f'"{w}"' for w in term.split() if len(w) > 1]
    return " OR ".join(dict.fromkeys(parts))


def _plain(text: str) -> str:
    """Lowercase, folded, diacritics removed (what FTS5 compares)."""
    decomposed = unicodedata.normalize("NFKD", fold(text).lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def term_coverage(query: str, text: str) -> float:
    """Share of the query's content terms present in text (prefix terms match word starts)."""
    terms = query_terms(query)
    if not terms:
        return 0.0
    sequence = _TOKEN.findall(_plain(text))
    words = set(sequence)
    joined = f" {' '.join(sequence)} "
    hit = 0
    for term, prefix in terms:
        term = _plain(term)
        if " " in term:  # phrase: its parts must stand next to each other
            hit += f" {term} " in joined
        elif (prefix and any(w.startswith(term) for w in words)) or (not prefix and term in words):
            hit += 1
    return hit / len(terms)


def excerpt(query: str, text: str, width: int = 160, lead: int = 30) -> str:
    """A preview of `text` where the query's words are: the window with the most different query terms (earliest on a
    tie), starting a little before the first of them at a word boundary, with "…" when it does not start the text.
    "Jakiej pogody…" as the sixth bullet of a slide shows that bullet, not the first one."""
    flat = " ".join(text.split())
    terms = [(_plain(t), prefix) for t, prefix in query_terms(query) if " " not in t]
    hits = []  # (position, term index) of every word that matches a term
    for m in _TOKEN.finditer(flat):
        word = _plain(m.group())
        for i, (term, prefix) in enumerate(terms):
            if word == term or (prefix and word.startswith(term)):
                hits.append((m.start(), i))
                break
    if not hits or hits[0][0] + 40 < width:  # nothing to find, or the first match is already in view
        return flat[:width]
    best, best_count = hits[0][0], 0
    for pos, _ in hits:
        count = len({i for p, i in hits if pos <= p < pos + width - lead})
        if count > best_count:
            best, best_count = pos, count
    if best + 40 < width:
        return flat[:width]
    start = flat.rfind(" ", 0, max(best - lead, 0)) + 1
    for mark in (". ", "? ", "! ", "• ", "; "):  # rather from the start of its sentence or bullet, if near
        cut = flat.rfind(mark, max(best - lead, 0), best)
        if cut != -1:
            start = max(start, cut + len(mark))
    if len(flat) - start < width - 1:  # near the end: fill the line with what comes before, from a boundary
        earliest = max(len(flat) - (width - 1), 0)
        marks = [flat.find(m, earliest, start) for m in (". ", "? ", "! ", "• ", "; ")]
        marks = [c + 2 for c in marks if c != -1]
        start = min(marks) if marks else (flat.find(" ", earliest, start) + 1 or start)
    if flat.startswith("• ", start):
        start += 2
    return ("…" if start else "") + flat[start : start + width - (1 if start else 0)]


# Runs of 64+ hex digits or base64 characters: embedded images and binary data (`0x151C2F…` in a SQL dump, base64
# in CSS/HTML). Base64 runs also need digits, both cases and few slashes: a long link path in generated docs
# (`org/apache/commons/math3/analysis/…`) is made of the same characters but is text.
_HEX_RUN = re.compile(r"(?:0x)?[0-9A-Fa-f]{64,}")
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/=]{64,}")


def _encoded(run: str) -> bool:
    return (
        run.count("/") * 20 <= len(run)
        and sum(ch.isdigit() for ch in run) >= 4
        and any(ch.isupper() for ch in run)
        and any(ch.islower() for ch in run)
    )


def is_blob(text: str | None) -> bool:
    """A passage that is mostly encoded data: it would match numbers and letter pairs by accident ("24") and its
    vector means nothing, so it is not indexed."""
    if not text:
        return False
    visible = sum(1 for ch in text if not ch.isspace())
    spans = [m.span() for m in _HEX_RUN.finditer(text)]
    spans += [m.span() for m in _BASE64_RUN.finditer(text) if _encoded(m.group())]
    covered = len({i for start, end in spans for i in range(start, end)})
    return visible > 0 and covered >= 0.5 * visible

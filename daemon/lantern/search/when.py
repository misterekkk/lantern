"""Time in queries, Polish and English: "zdjęcia z maja 2023", "photos from last summer", "faktura wczoraj".

An explicit phrase (a preposition with a month, season or year, a month with its year, a relative day, week, month or
year) is a hard filter: it is cut from the query, which is then searched only among files made in that time. A bare
year or month name ("wakacje 2023", "sierpień") only lifts files made then: the word may as well be in a document
(a report titled "2023") and stays in the query. Dates compare as wall-clock time (extractors/metadata.py).
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from lantern.extractors.metadata import EARLIEST, to_seconds
from lantern.textnorm import fold

_MONTHS = {
    1: ("styczen", "stycznia", "styczniu", "january"),
    2: ("luty", "lutego", "lutym", "february"),
    3: ("marzec", "marca", "marcu", "march"),
    4: ("kwiecien", "kwietnia", "kwietniu", "april"),
    5: ("maj", "maja", "maju", "may"),
    6: ("czerwiec", "czerwca", "czerwcu", "june"),
    7: ("lipiec", "lipca", "lipcu", "july"),
    8: ("sierpien", "sierpnia", "sierpniu", "august"),
    9: ("wrzesien", "wrzesnia", "wrzesniu", "september"),
    10: ("pazdziernik", "pazdziernika", "pazdzierniku", "october"),
    11: ("listopad", "listopada", "listopadzie", "november"),
    12: ("grudzien", "grudnia", "grudniu", "december"),
}
_SHORT = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}  # fmt: skip  (only before a year: "jan" is also a name)
MONTH = {w: m for m, words in _MONTHS.items() for w in words}
NOT_ALONE = {"may", "march"}  # English words first: a month only with a preposition or a year
SEASONS = {"spring": (3, 4, 5), "summer": (6, 7, 8), "autumn": (9, 10, 11), "winter": (12, 1, 2)}
SEASON = {
    **{w: "spring" for w in ("wiosna", "wiosny", "wiosne", "wiosnie", "spring")},
    **{w: "summer" for w in ("lato", "lata", "latem", "lecie", "summer")},
    **{w: "autumn" for w in ("jesien", "jesieni", "jesienia", "autumn", "fall")},
    **{w: "winter" for w in ("zima", "zimy", "zime", "zimie", "winter")},
}
SEASON_ADVERBS = {"latem", "zima", "wiosna", "jesienia"}  # "latem 2023": a time without a preposition
PREPOSITIONS = {"w", "we", "z", "ze", "in", "from", "during", "of"}
YEAR_WORDS = {"roku", "rok", "r", "year"}  # "w 2023 roku", "z roku 2023", "in the year 2023"
LAST = {"zeszlym", "zeszlego", "zeszlej", "ubieglym", "ubieglego", "ubieglej", "poprzednim", "poprzedniego",
        "poprzedniej", "last", "previous", "past"}  # fmt: skip
THIS = {"tym", "tego", "tej", "this"}
UNIT = {"tygodniu": "week", "tydzien": "week", "week": "week", "miesiacu": "month", "miesiac": "month",
        "month": "month", "roku": "year", "rok": "year", "year": "year"}  # fmt: skip
DAYS = {"dzis": 0, "dzisiaj": 0, "today": 0, "wczoraj": 1, "yesterday": 1, "przedwczoraj": 2}


@dataclass(frozen=True)
class TimeFilter:
    ranges: tuple[tuple[int, int], ...] = ()  # [start, end) in wall-clock seconds; empty: months of any year
    months: frozenset[int] = field(default_factory=frozenset)
    hard: bool = True

    def sql(self, column: str = "f.inferred_date") -> tuple[str, list]:
        if self.ranges:
            parts = " OR ".join(f"({column} >= ? AND {column} < ?)" for _ in self.ranges)
            return f"({parts})", [v for r in self.ranges for v in r]
        marks = ",".join("?" * len(self.months))
        return f"CAST(strftime('%m', {column}, 'unixepoch') AS INTEGER) IN ({marks})", sorted(self.months)

    def contains(self, seconds: int | None) -> bool:
        if seconds is None:
            return False
        if self.ranges:
            return any(a <= seconds < b for a, b in self.ranges)
        return (datetime(1970, 1, 1) + timedelta(seconds=seconds)).month in self.months


def _key(word: str) -> str:
    text = unicodedata.normalize("NFKD", fold(word).lower())
    return "".join(c for c in text if not unicodedata.combining(c)).strip(".,;:!?()\"'")


def _month_range(year: int, month: int) -> tuple[int, int]:
    start = datetime(year, month, 1)
    end = datetime(year + month // 12, month % 12 + 1, 1)
    return to_seconds(start), to_seconds(end)


def _year_range(year: int) -> tuple[int, int]:
    return to_seconds(datetime(year, 1, 1)), to_seconds(datetime(year + 1, 1, 1))


def _season_ranges(season: str, year: int) -> tuple[tuple[int, int], ...]:
    """Winter of a year: its January and February, and its December (both meanings of "zima 2023")."""
    months = SEASONS[season]
    if season == "winter":
        return (to_seconds(datetime(year, 1, 1)), to_seconds(datetime(year, 3, 1))), _month_range(year, 12)
    return ((_month_range(year, months[0])[0], _month_range(year, months[-1])[1]),)


def _season_occurrence(season: str, now: datetime, last: bool) -> tuple[int, int]:
    """This season (the one going on or this year's) or the last one that has ended."""

    def span(y: int) -> tuple[datetime, datetime]:
        if season == "winter":  # December of the year before to the end of February
            return datetime(y - 1, 12, 1), datetime(y, 3, 1)
        m = SEASONS[season]
        return datetime(y, m[0], 1), datetime(y + m[-1] // 12, m[-1] % 12 + 1, 1)

    year = now.year + (1 if season == "winter" and now.month == 12 else 0)
    start, end = span(year)
    if last:
        while end > now:
            year -= 1
            start, end = span(year)
    return to_seconds(start), to_seconds(end)


def _year(word: str, now: datetime) -> int | None:
    if re.fullmatch(r"(19|20)\d\d", word):
        y = int(word)
        if EARLIEST.year <= y <= now.year:
            return y
    return None


def _relative(keys: list[str], i: int, now: datetime) -> tuple[int, TimeFilter] | None:
    """(words used, filter) for a relative phrase starting at keys[i]."""
    today = datetime(now.year, now.month, now.day)
    w = keys[i]
    if w in DAYS:
        day = today - timedelta(days=DAYS[w])
        return 1, TimeFilter(((to_seconds(day), to_seconds(day + timedelta(days=1))),))
    j = i + 1 if w in PREPOSITIONS and i + 1 < len(keys) else i  # "w zeszłym tygodniu", "from last summer"
    if keys[j] == "the" and j + 1 < len(keys):
        j += 1
    if j + 1 >= len(keys) or (keys[j] not in LAST and keys[j] not in THIS):
        return None
    last, unit_word = keys[j] in LAST, keys[j + 1]
    used = j + 2 - i
    if unit_word in SEASON:
        return used, TimeFilter((_season_occurrence(SEASON[unit_word], now, last),))
    unit = UNIT.get(unit_word)
    if unit is None:
        return None
    if unit == "week":
        start = today - timedelta(days=today.weekday())
        start -= timedelta(days=7) if last else timedelta(0)
        return used, TimeFilter(((to_seconds(start), to_seconds(start + timedelta(days=7))),))
    if unit == "month":
        y, m = (now.year, now.month - 1) if last else (now.year, now.month)
        if m == 0:
            y, m = y - 1, 12
        return used, TimeFilter((_month_range(y, m),))
    return used, TimeFilter((_year_range(now.year - (1 if last else 0)),))


def _absolute(keys: list[str], i: int, now: datetime) -> tuple[int, TimeFilter] | None:
    """(words used, filter) for "[prep] [day] month|season [year]" or "[prep] [roku] year [roku]" at keys[i]."""
    n = len(keys)
    prep = keys[i] in PREPOSITIONS
    j = i + 1 if prep else i
    if j >= n:
        return None
    w = keys[j]
    if w == "the" and j + 1 < n:  # "in the summer of 2023"
        j += 1
        w = keys[j]

    def year_after(k: int) -> tuple[int | None, int]:
        if k < n and keys[k] == "of" and k + 1 < n:
            k += 1
        if k < n and keys[k] in YEAR_WORDS and k + 1 < n and _year(keys[k + 1], now):
            k += 1
        y = _year(keys[k], now) if k < n else None
        if y is not None and k + 1 < n and keys[k + 1] in YEAR_WORDS:
            return y, k + 2
        return y, (k + 1 if y is not None else k)

    # a day before the month: "14 maja 2023"
    day = None
    if re.fullmatch(r"([12]?\d|3[01])", w) and j + 1 < n and (keys[j + 1] in MONTH or keys[j + 1] in _SHORT):
        day, j = int(w), j + 1
        w = keys[j]
    month = MONTH.get(w) or (_SHORT.get(w) if day is not None or (j + 1 < n and _year(keys[j + 1], now)) else None)
    if month:
        k = j + 1
        if day is None and k < n and re.fullmatch(r"([12]?\d|3[01])", keys[k]):  # "May 14, 2023"
            day, k = int(keys[k]), k + 1
        year, end = year_after(k)
        if day is not None and year is not None:
            try:
                d = datetime(year, month, day)
            except ValueError:
                return None
            return end - i, TimeFilter(((to_seconds(d), to_seconds(d + timedelta(days=1))),))
        if year is not None:
            return end - i, TimeFilter((_month_range(year, month),))
        if day is not None:
            return None
        if prep:
            return j + 1 - i, TimeFilter(months=frozenset({month}))
        if w in NOT_ALONE:
            return None
        return j + 1 - i, TimeFilter(months=frozenset({month}), hard=False)
    if w in SEASON and (prep or w in SEASON_ADVERBS or (j + 1 < n and _year(keys[j + 1], now))):
        if w == "fall" and not prep and not (j + 1 < n and _year(keys[j + 1], now)):
            return None
        year, end = year_after(j + 1)
        if year is not None:
            return end - i, TimeFilter(_season_ranges(SEASON[w], year))
        return j + 1 - i, TimeFilter(months=frozenset(SEASONS[SEASON[w]]))
    if prep or w in YEAR_WORDS:
        k = j + 1 if w in YEAR_WORDS else j
        year = _year(keys[k], now) if k < n else None
        if year is not None and (prep or w in YEAR_WORDS):
            end = k + 2 if k + 1 < n and keys[k + 1] in YEAR_WORDS else k + 1
            return end - i, TimeFilter((_year_range(year),))
    year = _year(w, now)
    if year is not None and not prep:
        return 1, TimeFilter((_year_range(year),), hard=False)
    return None


def _dotted(word: str, now: datetime) -> TimeFilter | None:
    """14.05.2023 or 2023-05-14 in the query."""
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.((?:19|20)\d\d)", word) or None
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.fullmatch(r"((?:19|20)\d\d)-(\d{2})-(\d{2})", word)
        if not m:
            return None
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        day = datetime(y, mo, d)
    except ValueError:
        return None
    if not (EARLIEST <= day <= now):
        return None
    return TimeFilter(((to_seconds(day), to_seconds(day + timedelta(days=1))),))


def parse(query: str, now: datetime | None = None) -> tuple[str, TimeFilter | None]:
    """(query without a hard time phrase, the filter) — or the query unchanged with a soft filter, or None."""
    now = now or datetime.now()
    words = query.split()
    keys = [_key(w) for w in words]
    soft: TimeFilter | None = None
    for i in range(len(words)):
        if not keys[i]:
            continue
        dotted = _dotted(keys[i], now)
        if dotted:
            return " ".join(words[:i] + words[i + 1 :]), dotted
        found = _relative(keys, i, now) or _absolute(keys, i, now)
        if found is None:
            continue
        used, tf = found
        if tf.hard:
            return " ".join(words[:i] + words[i + used :]), tf
        soft = soft or tf
    return query, soft

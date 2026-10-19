"""GPS coordinates -> the nearest city, offline, from the bundled GeoNames table (data/places.tsv.gz, CC-BY 4.0,
built by scripts/build_places.py). Only cities the user's files were made in are copied into the index (`places`,
`place_names`), so place words in queries match places the user has been to, in any language GeoNames knows.

Apple's CLGeocoder was rejected: it sends the coordinates to Apple.
"""

import gzip
import json
import math
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from lantern.textnorm import fold

DATA = Path(__file__).with_name("data") / "places.tsv.gz"
MAX_KM = 40.0  # farther from every city of 15 000+ people: no place (a lake, the sea, wilderness)
EARTH_KM = 6371.0


@dataclass(frozen=True)
class Place:
    id: int
    name: str
    country: str
    lat: float
    lon: float
    display: dict[str, str]
    names: tuple[str, ...]


@lru_cache(maxsize=1)
def _table() -> tuple[np.ndarray, np.ndarray]:
    """(GeoNames ids, unit vectors of their coordinates) for nearest-city lookups; names are read only when needed."""
    ids, coords = [], []
    with gzip.open(DATA, "rt", encoding="utf-8") as f:
        for line in f:
            gid, lat, lon, _ = line.split("\t", 3)
            ids.append(int(gid))
            coords.append((float(lat), float(lon)))
    ids_a = np.array(ids, dtype=np.int64)
    lat_r, lon_r = np.radians(np.array(coords)).T
    unit = np.stack([np.cos(lat_r) * np.cos(lon_r), np.cos(lat_r) * np.sin(lon_r), np.sin(lat_r)], axis=1)
    return ids_a, unit  # float64: in float32, cosines of points a few km apart are all 1.0


def nearest(lat: float, lon: float, max_km: float = MAX_KM) -> int | None:
    ids, unit = _table()
    la, lo = math.radians(lat), math.radians(lon)
    q = np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])
    dots = unit @ q
    best = int(np.argmax(dots))
    km = EARTH_KM * math.acos(max(-1.0, min(1.0, float(dots[best]))))
    return int(ids[best]) if km <= max_km else None


def load(place_ids: set[int]) -> list[Place]:
    """Full rows (names in every language) of these cities from the bundled table."""
    out = []
    wanted = {str(i) for i in place_ids}
    with gzip.open(DATA, "rt", encoding="utf-8") as f:
        for line in f:
            gid = line[: line.index("\t")]
            if gid not in wanted:
                continue
            _, lat, lon, country, name, shown, other = line.rstrip("\n").split("\t")
            display = dict(p.split("=", 1) for p in shown.split("|") if "=" in p)
            names = (name, *display.values(), *(n for n in other.split("|") if n))
            out.append(Place(int(gid), name, country, float(lat), float(lon), display, names))
    return out


def name_key(text: str) -> str:
    """Folded, lower case, without accents or punctuation: "Kraków" -> "krakow", "São Paulo" -> "sao paulo"."""
    text = unicodedata.normalize("NFKD", fold(text).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"\w+", text))


def store(conn: sqlite3.Connection, places: list[Place]) -> None:
    for p in places:
        conn.execute(
            "INSERT OR REPLACE INTO places(id, name, country, lat, lon, display) VALUES (?, ?, ?, ?, ?, ?)",
            (p.id, p.name, p.country, p.lat, p.lon, json.dumps(p.display, ensure_ascii=False)),
        )
        keys = {name_key(n) for n in p.names}
        conn.executemany(
            "INSERT OR IGNORE INTO place_names(name, place_id) VALUES (?, ?)",
            [(k, p.id) for k in keys if len(k) >= 3 and not k.isdigit()],
        )


def drop_unused(conn: sqlite3.Connection) -> None:
    """Places no file points at any more (files deleted, the setting switched off) are removed with their names."""
    conn.execute("DELETE FROM places WHERE id NOT IN (SELECT place_id FROM files WHERE place_id IS NOT NULL)")


def display_name(name: str, display_json: str, lang: str) -> str:
    """The city's name in the user's language ("pl", "de", "zh-Hans" -> zh-CN …), else GeoNames' name."""
    try:
        names = json.loads(display_json or "{}")
    except ValueError:
        return name
    base = lang.split("-")[0].split("_")[0].lower()
    for key in (lang, lang.replace("_", "-"), base, "zh-CN" if base == "zh" else None):
        if key and key in names:
            return names[key]
    return name


# ---------- place words in queries ----------

# A place after one of these is a filter ("zdjęcia z Krakowa", "photos in Kraków"); a bare place name only lifts the
# files made there (the word may as well be in a document's text).
PREPOSITIONS = {"w", "we", "z", "ze", "na", "kolo", "pod", "in", "at", "from", "near", "around", "en", "im", "aus",
                "bei", "dans", "di", "da"}  # fmt: skip
MIN_WORD = 4


@dataclass(frozen=True)
class PlaceMatch:
    place_ids: tuple[int, ...]
    start: int  # word span in the query (index of the first and one past the last word, preposition included)
    end: int
    hard: bool


# Case endings (Polish and its neighbours, folded): "Krakowa", "Krakowie", "Warszawie", "Gdańsku", "Paryżem"
ENDINGS = {"", "a", "u", "e", "ie", "y", "i", "em", "iem", "owi", "ach", "ami", "o"}


def _close(word: str, name: str) -> bool:
    """The name or an inflected form: all of the name (or all but its last letter, "Warszaw|a" -> "Warszaw|ie")
    followed by a case ending. "Kraken" is not "Kraków"."""
    if word == name:
        return True
    if len(name) < MIN_WORD:
        return False
    return any(word.startswith(name[:keep]) and word[keep:] in ENDINGS for keep in (len(name), len(name) - 1))


_names_cache: dict[str, tuple[int, dict]] = {}


def _known_names(conn: sqlite3.Connection) -> dict[str, list[tuple[list[str], int]]]:
    """Place names of the index grouped by their first letters, cached until the places change."""
    db_file = conn.execute("PRAGMA database_list").fetchone()[2]
    stamp = conn.execute("SELECT count(*), coalesce(sum(id), 0) FROM places").fetchone()
    key = hash(tuple(stamp))
    cached = _names_cache.get(db_file)
    if cached and cached[0] == key:
        return cached[1]
    by_first: dict[str, list[tuple[list[str], int]]] = {}
    for name, pid in conn.execute("SELECT name, place_id FROM place_names"):
        parts = name.split()
        by_first.setdefault(parts[0][:MIN_WORD], []).append((parts, pid))
    _names_cache[db_file] = (key, by_first)
    return by_first


def find_in_query(conn: sqlite3.Connection, query: str) -> PlaceMatch | None:
    by_first = _known_names(conn)
    if not by_first:
        return None
    words = query.split()
    keys = [name_key(w) for w in words]
    for n in (3, 2, 1):  # longest names first ("Nowy Sącz", "New York")
        for i in range(len(words) - n + 1):
            span = keys[i : i + n]
            if not all(span) or len(span[0]) < 3:
                continue
            hits = set()
            for parts, pid in by_first.get(span[0][:MIN_WORD], []):
                if (
                    len(parts) == n
                    and all(a == b for a, b in zip(span[:-1], parts[:-1], strict=False))
                    and _close(span[-1], parts[-1])
                ):
                    hits.add(pid)
            if hits:
                hard = i > 0 and keys[i - 1] in PREPOSITIONS
                return PlaceMatch(tuple(sorted(hits)), i - 1 if hard else i, i + n, hard)
    return None

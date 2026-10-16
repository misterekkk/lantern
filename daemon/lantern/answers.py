"""Instant answers computed on the Mac, like Spotlight's: arithmetic, unit conversions and the time in other cities.

No network: arithmetic is parsed with `ast` (never `eval`), units come from a fixed table, time zones from the system's
tz database (zoneinfo). Currency conversion would need exchange rates from the internet, so it is not here.
Queries may be Polish or English and use a decimal comma ("2,5 km na mile", "godzina w nowym jorku").
"""

import ast
import math
import operator
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, available_timezones

from lantern.textnorm import fold


@dataclass(frozen=True)
class Answer:
    kind: str  # math | unit | time
    title: str  # the answer itself: "42", "3,11 mi", "12:35"
    subtitle: str  # what was asked, written out: "2 + 2 × 20", "5 km = 3,11 mi", "Nowy Jork · −6 h"
    copy: str  # what ↩ copies


def _number(text: str) -> float:
    return float(text.replace(" ", "").replace(" ", "").replace(",", "."))


def _format(value: float, digits: int = 10, comma: bool = False) -> str:
    if math.isfinite(value) and abs(value - round(value)) < 1e-9 and abs(value) < 1e15:
        text = str(int(round(value)))
    else:
        text = f"{value:.{digits}g}"
    return text.replace(".", ",") if comma else text


# ---------- arithmetic ----------

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv}  # fmt: skip
_FUNCS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "log": math.log10, "ln": math.log,
          "abs": abs, "round": round, "exp": math.exp, "pierwiastek": math.sqrt}  # fmt: skip
_CONSTS = {"pi": math.pi, "e": math.e}
_MATH_CHARS = re.compile(r"^[\d\s.,+\-*/×÷^%()a-z]+$")


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
        value = _eval(node.operand)
        return -value if isinstance(node.op, ast.USub) else value
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCS
        and len(node.args) == 1
    ):
        return _FUNCS[node.func.id](_eval(node.args[0]))
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    raise ValueError("not arithmetic")


def arithmetic(query: str) -> Answer | None:
    q = query.strip().lower().rstrip("=").strip()
    if not q or not _MATH_CHARS.match(q) or not re.search(r"\d", q):
        return None
    if not re.search(r"[+\-*/×÷^%(]|sqrt|pierwiastek|sin|cos|tan|log|ln", q):
        return None  # a plain number is not a calculation
    comma = "," in q and "." not in q
    expr = q.replace("×", "*").replace("÷", "/").replace("^", "**")
    expr = re.sub(r"(\d),(\d)", r"\1.\2", expr)  # decimal comma
    expr = re.sub(r"(\d+(?:\.\d+)?)%", r"(\1/100)", expr)  # 15% -> 0.15
    try:
        value = _eval(ast.parse(expr, mode="eval"))
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError, RecursionError):
        return None
    if not isinstance(value, int | float) or not math.isfinite(value):
        return None
    text = _format(float(value), comma=comma)
    return Answer("math", text, query.strip().rstrip("=").strip().replace("*", "×"), text)


# ---------- units ----------

# unit -> (dimension, factor to the base unit); temperatures are handled separately.
_UNITS: dict[str, tuple[str, float]] = {}
_NAMES: dict[str, str] = {}  # spelling -> unit


def _unit(code: str, dim: str, factor: float, *names: str) -> None:
    _UNITS[code] = (dim, factor)
    for name in (code, *names):
        _NAMES[fold(name).lower()] = code
    # Polish "w <unit>ach" (w funtach, w metrach, w calach): from the first Polish name.
    polish = next((n for n in names if re.fullmatch(r"[a-ząćęłńóśźż]+", n) and not n.endswith(("s", "et"))), None)
    if polish:
        stem = re.sub(r"(a|y|i|ow|e|o)$", "", fold(polish).lower())
        _NAMES.setdefault(stem + "ach", code)


for _code, _f, _names in [
    ("mm", 0.001, ("milimetr", "milimetry", "milimetrow", "millimeter", "millimeters")),
    ("cm", 0.01, ("centymetr", "centymetry", "centymetrow", "centimeter", "centimeters")),
    ("m", 1, ("metr", "metry", "metrow", "meter", "meters", "metre", "metres")),
    ("km", 1000, ("kilometr", "kilometry", "kilometrow", "kilometer", "kilometers")),
    ("in", 0.0254, ("cal", "cale", "cali", "inch", "inches", '"')),
    ("ft", 0.3048, ("stopa", "stopy", "stop", "foot", "feet")),
    ("yd", 0.9144, ("jard", "jardy", "jardow", "yard", "yards")),
    ("mi", 1609.344, ("mila", "mile", "mil", "mile", "miles")),
]:
    _unit(_code, "length", _f, *_names)
for _code, _f, _names in [
    ("mg", 1e-6, ("miligram", "miligramy", "milligram", "milligrams")),
    ("g", 0.001, ("gram", "gramy", "gramow", "grams")),
    ("kg", 1, ("kilogram", "kilogramy", "kilogramow", "kilo", "kilograms")),
    ("t", 1000, ("tona", "tony", "ton", "tonne", "tonnes")),
    ("oz", 0.028349523125, ("uncja", "uncje", "uncji", "ounce", "ounces")),
    ("lb", 0.45359237, ("funt", "funty", "funtow", "pound", "pounds", "lbs")),
]:
    _unit(_code, "mass", _f, *_names)
for _code, _f, _names in [
    ("ml", 0.001, ("mililitr", "mililitry", "milliliter", "milliliters")),
    ("l", 1, ("litr", "litry", "litrow", "liter", "liters", "litre", "litres")),
    ("gal", 3.785411784, ("galon", "galony", "galonow", "gallon", "gallons")),
    ("cup", 0.2365882365, ("szklanka", "szklanki", "cups")),
]:
    _unit(_code, "volume", _f, *_names)
for _code, _f, _names in [
    ("km/h", 1 / 3.6, ("kmh", "kph")), ("m/s", 1, ("ms",)), ("mph", 0.44704, ()),
    ("kn", 0.514444, ("wezel", "wezlach", "knot", "knots")),
]:  # fmt: skip
    _unit(_code, "speed", _f, *_names)
for _code, _f, _names in [
    ("B", 1, ("bajt", "bajty", "bajtow", "byte", "bytes")), ("KB", 1e3, ("kilobajt", "kilobajty")),
    ("MB", 1e6, ("megabajt", "megabajty", "megabajtow")), ("GB", 1e9, ("gigabajt", "gigabajty", "gigabajtow")),
    ("TB", 1e12, ("terabajt", "terabajty")), ("KiB", 1024, ()), ("MiB", 1024**2, ()), ("GiB", 1024**3, ()),
]:  # fmt: skip
    _unit(_code, "data", _f, *_names)
for _code, _f, _names in [
    ("s", 1, ("sekunda", "sekundy", "sekund", "sec", "second", "seconds")),
    ("min", 60, ("minuta", "minuty", "minut", "minute", "minutes")),
    ("h", 3600, ("godzina", "godziny", "godzin", "hour", "hours", "godz")),
    ("d", 86400, ("dzien", "dni", "dniach", "day", "days")),
    ("wk", 604800, ("tydzien", "tygodnie", "tygodni", "tygodniach", "week", "weeks")),
]:
    _unit(_code, "time", _f, *_names)
_TEMPS = {"c": "°C", "°c": "°C", "celsius": "°C", "celsjusz": "°C", "celsjusza": "°C", "stopni celsjusza": "°C",
          "f": "°F", "°f": "°F", "fahrenheit": "°F", "fahrenheita": "°F", "k": "K", "kelvin": "K", "kelwin": "K",
          "kelwina": "K", "kelwinow": "K"}  # fmt: skip

_CONVERSION = re.compile(
    r"^\s*(-?\d[\d\s ]*(?:[.,]\d+)?)\s*([^\d=]+?)\s+(?:to|in|into|na|w|we|->|=|→)\s+([^\d=]+?)\s*$", re.I
)


def _temperature(value: float, src: str, dst: str) -> float:
    celsius = {"°C": value, "°F": (value - 32) * 5 / 9, "K": value - 273.15}[src]
    return {"°C": celsius, "°F": celsius * 9 / 5 + 32, "K": celsius + 273.15}[dst]


def conversion(query: str) -> Answer | None:
    m = _CONVERSION.match(query)
    if not m:
        return None
    value, raw_src, raw_dst = _number(m.group(1)), fold(m.group(2)).lower().strip(), fold(m.group(3)).lower().strip()
    comma = "," in m.group(1)
    if raw_src in _TEMPS and raw_dst in _TEMPS:
        src, dst = _TEMPS[raw_src], _TEMPS[raw_dst]
        result = _temperature(value, src, dst)
    else:
        src, dst = _NAMES.get(raw_src), _NAMES.get(raw_dst)
        if not src or not dst or _UNITS[src][0] != _UNITS[dst][0] or src == dst:
            return None
        result = value * _UNITS[src][1] / _UNITS[dst][1]
    shown = f"{_format(round(result, 6), 8, comma)} {dst}"
    return Answer("unit", shown, f"{_format(value, comma=comma)} {src} = {shown}", _format(round(result, 6), 8, comma))


# ---------- time in other cities ----------

# Polish (and a few other) names of cities whose tz database name differs; the rest is matched in English.
_CITY_ALIASES = {
    "nowy jork": "America/New_York", "nowym jorku": "America/New_York", "new york": "America/New_York",
    "nyc": "America/New_York",
    "londyn": "Europe/London", "londynie": "Europe/London", "paryz": "Europe/Paris", "paryzu": "Europe/Paris",
    "tokio": "Asia/Tokyo", "pekin": "Asia/Shanghai", "pekinie": "Asia/Shanghai", "moskwa": "Europe/Moscow",
    "moskwie": "Europe/Moscow", "waszyngton": "America/New_York", "waszyngtonie": "America/New_York",
    "rzym": "Europe/Rome", "rzymie": "Europe/Rome", "berlinie": "Europe/Berlin", "madrycie": "Europe/Madrid",
    "wiedniu": "Europe/Vienna", "wieden": "Europe/Vienna", "pradze": "Europe/Prague", "praga": "Europe/Prague",
    "kijow": "Europe/Kyiv", "kijowie": "Europe/Kyiv", "san francisco": "America/Los_Angeles",
    "kalifornii": "America/Los_Angeles", "sydney": "Australia/Sydney", "dubaju": "Asia/Dubai", "dubaj": "Asia/Dubai",
    "singapurze": "Asia/Singapore", "seulu": "Asia/Seoul", "seul": "Asia/Seoul", "warszawie": "Europe/Warsaw",
    "lizbonie": "Europe/Lisbon", "lizbona": "Europe/Lisbon", "atenach": "Europe/Athens", "ateny": "Europe/Athens",
    "stambule": "Europe/Istanbul", "stambul": "Europe/Istanbul", "kairze": "Africa/Cairo", "kair": "Africa/Cairo",
    "meksyku": "America/Mexico_City", "chicago": "America/Chicago", "toronto": "America/Toronto",
}  # fmt: skip
_TIME_QUERY = re.compile(
    r"^\s*(?:time|current time|what time is it|godzina|czas|ktora godzina|ktora jest godzina)"
    r"\s+(?:in|w|we)\s+(.+?)\s*\??$"
)


def _zone_index() -> dict[str, str]:
    index = {}
    for name in available_timezones():
        city = name.rsplit("/", 1)[-1].replace("_", " ").lower()
        if "/" in name and not name.startswith(("Etc/", "SystemV/")):
            index.setdefault(city, name)
    return index


_ZONES: dict[str, str] | None = None


def world_time(query: str, now: datetime | None = None) -> Answer | None:
    global _ZONES
    m = _TIME_QUERY.match(fold(query).lower())
    if not m:
        return None
    if _ZONES is None:
        _ZONES = _zone_index()
    place = m.group(1).strip()
    zone = _CITY_ALIASES.get(place) or _ZONES.get(place)
    if not zone:
        return None
    here = now or datetime.now().astimezone()
    there = here.astimezone(ZoneInfo(zone))
    diff_h = (there.utcoffset() - here.utcoffset()).total_seconds() / 3600
    sign = "+" if diff_h > 0 else "−" if diff_h < 0 else "±"
    diff = f"{sign}{_format(abs(diff_h))} h"
    city = zone.rsplit("/", 1)[-1].replace("_", " ")
    title = there.strftime("%H:%M")
    return Answer("time", title, f"{city} · {there.strftime('%Y-%m-%d')} · {diff}", title)


def instant_answer(query: str) -> Answer | None:
    """The first instant answer that fits the query, or None (most queries)."""
    if len(query) > 80:
        return None
    return arithmetic(query) or conversion(query) or world_time(query)

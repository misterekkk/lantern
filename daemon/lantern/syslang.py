"""The user's macOS languages (AppleLanguages): a language cue for short photo queries, and the preferred languages
for app names and text recognition (plus the ones added in Settings, see `preferred_languages`)."""

import os
import re
import subprocess
from functools import cache

_CODE = re.compile(r'"?([a-z]{2,3})(?:[-_]([A-Za-z]{2,4}))?(?:[-_][A-Za-z0-9]+)*"?')


def normalize_language(raw: str) -> str | None:
    """`pl-PL`, `pl_PL`, `PL` -> "pl"; script variants keep the script (`zh-Hans`); None if it is not a code."""
    m = _CODE.fullmatch(raw.strip().lower()) if raw else None
    if not m:
        return None
    lang, extra = m.group(1), m.group(2)
    return f"{lang}-{extra.capitalize()}" if extra and len(extra) == 4 else lang


@cache
def system_languages() -> tuple[str, ...]:
    """Preferred languages of macOS in order (System Settings > General > Language & Region)."""
    try:
        out = subprocess.run(
            ["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True, timeout=3
        ).stdout
        found = [normalize_language(m.group(0)) for m in _CODE.finditer(out)]
        langs = tuple(dict.fromkeys(code for code in found if code))
        if langs:
            return langs
    except (OSError, subprocess.SubprocessError):
        pass
    return (normalize_language(os.environ.get("LANG", "en")[:2]) or "en",)


def system_language() -> str:
    return system_languages()[0].split("-")[0]


def preferred_languages(extra: list[str] | tuple[str, ...] = ()) -> list[str]:
    """macOS languages, then the ones added in Settings, then English (names of most apps); no duplicates. App and
    setting names come from macOS, so they are shown in its language whatever language Lantern's window uses."""
    codes = [*system_languages(), *(normalize_language(c) for c in extra), "en"]
    return list(dict.fromkeys(c for c in codes if c))

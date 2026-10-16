"""Word definitions from the dictionaries installed in macOS (Dictionary.app), like Spotlight's: DictionaryServices
through ctypes, on the Mac, no network. Which dictionaries answer is set in Dictionary.app's settings."""

import ctypes
import re
from dataclasses import dataclass
from functools import cache

_WORD = re.compile(r"^[^\W\d_][^\W\d_'-]{2,39}$")
_UTF8 = 0x08000100


@dataclass(frozen=True)
class Definition:
    word: str
    text: str  # the dictionary entry as plain text (first lines)


class _CFRange(ctypes.Structure):
    _fields_ = [("location", ctypes.c_long), ("length", ctypes.c_long)]


@cache
def _frameworks():
    try:
        cs = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreServices.framework/CoreServices")
        cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    except OSError:
        return None
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringGetLength.restype, cf.CFStringGetLength.argtypes = ctypes.c_long, [ctypes.c_void_p]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cs.DCSCopyTextDefinition.restype = ctypes.c_void_p
    cs.DCSCopyTextDefinition.argtypes = [ctypes.c_void_p, ctypes.c_void_p, _CFRange]
    return cs, cf


def define(query: str, max_chars: int = 280) -> Definition | None:
    """A definition for a single word (3+ letters), or None."""
    word = query.strip()
    if not _WORD.match(word) or (fw := _frameworks()) is None:
        return None
    cs, cf = fw
    raw = word.encode()
    text_ref = cf.CFStringCreateWithCString(None, raw, _UTF8)
    if not text_ref:
        return None
    try:
        entry = cs.DCSCopyTextDefinition(None, text_ref, _CFRange(0, cf.CFStringGetLength(text_ref)))
    finally:
        cf.CFRelease(text_ref)
    if not entry:
        return None
    try:
        buf = ctypes.create_string_buffer(cf.CFStringGetLength(entry) * 4 + 1)
        if not cf.CFStringGetCString(entry, buf, len(buf), _UTF8):
            return None
        text = " ".join(buf.value.decode().split())
    finally:
        cf.CFRelease(entry)
    if not text.casefold().startswith(word.casefold()[:3]):
        return None  # an entry for another word (the dictionary matched only a part of the query)
    return Definition(word, text[:max_chars].rstrip() + ("…" if len(text) > max_chars else ""))

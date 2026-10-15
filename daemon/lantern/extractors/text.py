"""Plain text / source code extraction. Opens files read-only and never writes to them."""

from pathlib import Path

from charset_normalizer import from_bytes

from lantern.config import MAX_TEXT_FILE_BYTES


class SkipFile(Exception):
    """The file should not be indexed (binary, too large, undecodable)."""


def extract_text(path: Path, max_bytes: int = MAX_TEXT_FILE_BYTES) -> str:
    with open(path, "rb") as f:  # read-only
        data = f.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise SkipFile(f"larger than {max_bytes} bytes")
    if b"\x00" in data[:8192]:
        raise SkipFile("binary content")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        best = from_bytes(data).best()
        if best is None:
            raise SkipFile("unknown text encoding") from None
        text = str(best)
    return text.replace("\r\n", "\n").lstrip("﻿")

"""Line-based overlapping windows for prose and as the fallback for code; Markdown is split by headings first."""

import re
from dataclasses import dataclass

# ~400 tokens of mixed PL/EN text or code (rough 4 chars/token), 15% overlap.
WINDOW_CHARS = 1600
OVERLAP_CHARS = 240


@dataclass(frozen=True)
class Piece:
    text: str
    start_line: int  # 1-based, inclusive
    end_line: int
    symbol: str | None = None


def _split_long_line(line: str, line_no: int, symbol: str | None) -> list[Piece]:
    """Minified code, notebook JSON, CSV rows: one line can exceed a window, so cut it into pieces."""
    step = WINDOW_CHARS - OVERLAP_CHARS
    return [
        Piece(part.strip(), line_no, line_no, symbol)
        for start in range(0, len(line), step)
        if (part := line[start : start + WINDOW_CHARS]).strip()
    ]


def windows(lines: list[str], first_line: int = 1, symbol: str | None = None) -> list[Piece]:
    """Split lines into windows of ~WINDOW_CHARS that never cut a normal line, overlapping by ~OVERLAP_CHARS.
    Lines longer than a window are cut on their own."""
    if any(len(line) > WINDOW_CHARS for line in lines):
        pieces: list[Piece] = []
        run_start = 0
        for i, line in enumerate(lines + [""]):
            if i == len(lines) or len(line) > WINDOW_CHARS:
                if i > run_start:
                    pieces.extend(windows(lines[run_start:i], first_line + run_start, symbol))
                if i < len(lines):
                    pieces.extend(_split_long_line(line, first_line + i, symbol))
                run_start = i + 1
        return pieces

    pieces = []
    i, n = 0, len(lines)
    while i < n:
        size, j = 0, i
        while j < n and (size == 0 or size + len(lines[j]) + 1 <= WINDOW_CHARS):
            size += len(lines[j]) + 1
            j += 1
        text = "\n".join(lines[i:j]).strip()
        if text:
            pieces.append(Piece(text, first_line + i, first_line + j - 1, symbol))
        if j >= n:
            break
        # step back for overlap, but always make progress
        back, k = 0, j
        while k > i + 1 and back + len(lines[k - 1]) + 1 <= OVERLAP_CHARS:
            back += len(lines[k - 1]) + 1
            k -= 1
        i = k
    return pieces


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def chunk_text(text: str, markdown: bool = False) -> list[Piece]:
    lines = text.split("\n")
    if not markdown:
        return windows(lines)
    pieces: list[Piece] = []
    start, heading = 0, None
    in_fence = False
    for idx, line in enumerate(lines + ["# end"]):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(line)
        if m or idx == len(lines):
            if idx > start:
                pieces.extend(windows(lines[start:idx], first_line=start + 1, symbol=heading))
            if m:
                start, heading = idx, m.group(2)
    return pieces

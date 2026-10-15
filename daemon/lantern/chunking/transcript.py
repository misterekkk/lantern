"""Group transcript segments (Whisper) into ~30 s chunks with timestamps."""

from dataclasses import dataclass

from lantern.config import TRANSCRIPT_CHUNK_S


@dataclass(frozen=True)
class Segment:
    start: float  # seconds
    end: float
    text: str


@dataclass(frozen=True)
class TimedPiece:
    text: str
    start_ms: int
    end_ms: int


def group_segments(segments: list[Segment], target_s: float = TRANSCRIPT_CHUNK_S) -> list[TimedPiece]:
    pieces: list[TimedPiece] = []
    current: list[Segment] = []
    for seg in segments:
        if not seg.text.strip():
            continue
        current.append(seg)
        if seg.end - current[0].start >= target_s:
            pieces.append(_piece(current))
            current = []
    if current:
        pieces.append(_piece(current))
    return pieces


def _piece(segs: list[Segment]) -> TimedPiece:
    text = " ".join(s.text.strip() for s in segs)
    return TimedPiece(text, int(segs[0].start * 1000), int(segs[-1].end * 1000))

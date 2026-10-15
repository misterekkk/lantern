"""Chunkers: code by functions/classes (tree-sitter) with overlapping-window fallback; text by headings/windows."""

from pathlib import Path

from lantern.chunking.code import chunk_code
from lantern.chunking.text import Piece, chunk_text
from lantern.extractors import language_for

__all__ = ["Piece", "chunk_file"]


DATA_EXTENSIONS = frozenset({".csv", ".tsv"})
DATA_MIN_CHARS = 16_000  # small data files are cheap: index them fully
HEAD_WINDOWS = 2


def is_data_like(text: str) -> bool:
    """Numbers and punctuation rather than words (CSV dumps, model files, serialized arrays)."""
    sample = "".join(text[:20_000].split())
    if not sample:
        return False
    letters = sum(ch.isalpha() for ch in sample)
    return letters / len(sample) < 0.5


def chunk_file(path: Path, kind: str, text: str) -> list[Piece]:
    if kind == "code":
        language = language_for(path)
        if language:
            return chunk_code(text, language)
    if len(text) > DATA_MIN_CHARS and (path.suffix.lower() in DATA_EXTENSIONS or is_data_like(text)):
        # Head only (header + first rows): findable by columns/topic without embedding thousands of rows.
        return chunk_text(text[: 2 * DATA_MIN_CHARS])[:HEAD_WINDOWS]
    return chunk_text(text, markdown=path.suffix.lower() in {".md", ".markdown"})

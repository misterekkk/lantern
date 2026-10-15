from pathlib import Path

from lantern.chunking import chunk_file
from lantern.chunking.text import WINDOW_CHARS, chunk_text, windows

PY = '''import os


def top_level(a, b):
    """Add numbers."""
    return a + b


class Store:
    """Keeps things."""

    limit = 10

    def get(self, key):
        return key

    @staticmethod
    def build():
        return Store()
'''


def test_python_functions_and_classes():
    pieces = chunk_file(Path("m.py"), "code", PY)
    symbols = {p.symbol for p in pieces}
    assert {"top_level", "Store"} <= symbols
    store = next(p for p in pieces if p.symbol == "Store")
    assert "def get" in store.text and store.start_line == 9


def test_big_class_is_split_into_methods():
    methods = "\n".join(f"    def m{i}(self):\n" + "        x = 1\n" * 30 for i in range(12))
    src = f"class Big:\n    '''Doc.'''\n\n{methods}\n"
    pieces = chunk_file(Path("big.py"), "code", src)
    symbols = [p.symbol for p in pieces]
    assert "Big.m0" in symbols and "Big.m11" in symbols
    assert any(p.symbol == "Big" and "class Big" in p.text for p in pieces)  # header kept


def test_typescript_exported_function():
    src = "import x from 'y'\n\nexport function retryRequest(url: string) {\n  return fetch(url)\n}\n"
    pieces = chunk_file(Path("a.ts"), "code", src)
    assert any(p.symbol == "retryRequest" for p in pieces)
    assert any("import x" in p.text for p in pieces)  # uncovered top-level code is kept


def test_every_line_is_covered():
    src = PY + "\nprint(top_level(1, 2))\n"
    pieces = chunk_file(Path("m.py"), "code", src)
    covered = set()
    for p in pieces:
        covered.update(range(p.start_line, p.end_line + 1))
    non_empty = {i + 1 for i, line in enumerate(src.split("\n")) if line.strip()}
    assert non_empty <= covered


def test_windows_overlap_and_size():
    lines = [f"linia numer {i} z jakimś tekstem" for i in range(400)]
    pieces = windows(lines)
    assert len(pieces) > 1
    assert all(len(p.text) <= WINDOW_CHARS for p in pieces)
    for a, b in zip(pieces, pieces[1:], strict=False):
        assert b.start_line <= a.end_line  # overlap
        assert b.start_line > a.start_line  # progress


def test_markdown_sections():
    md = "# Wstęp\nTekst wstępu.\n\n## Instalacja\n```\n# not a heading\n```\nKroki.\n"
    pieces = chunk_text(md, markdown=True)
    assert [p.symbol for p in pieces] == ["Wstęp", "Instalacja"]
    assert "# not a heading" in pieces[1].text


def test_unknown_language_falls_back_to_windows():
    pieces = chunk_file(Path("x.unknownlang"), "code", "a\nb\n")
    assert pieces and pieces[0].text == "a\nb"


def test_very_long_line_is_cut():
    minified = "var a=1;" * 2000  # 16k chars on one line
    pieces = windows(["// header", minified, "// footer"])
    assert all(len(p.text) <= WINDOW_CHARS for p in pieces)
    assert pieces[0].text == "// header" and pieces[-1].text == "// footer"
    assert sum(1 for p in pieces if p.start_line == 2) >= 10


def test_large_csv_is_indexed_by_head_only():
    csv = "date,city,temperature,humidity\n" + "".join(
        f"2024-01-{i % 28 + 1:02d},Warszawa,{i % 30}.5,{i % 90}\n" for i in range(5000)
    )
    pieces = chunk_file(Path("weather.csv"), "text", csv)
    assert len(pieces) <= 2 and pieces[0].text.startswith("date,city,temperature")


def test_numeric_dump_is_indexed_by_head_only_but_prose_fully():
    dump = "Tree=0\n" + "\n".join(f"leaf_value={i * 0.0137:.6f} {i * 3.1:.4f} {-i:.3f}" for i in range(4000))
    assert len(chunk_file(Path("model.txt"), "text", dump)) <= 2
    prose = "To jest długi dokument o drzewach decyzyjnych i ich przycinaniu. " * 600
    assert len(chunk_file(Path("notes.txt"), "text", prose)) > 10

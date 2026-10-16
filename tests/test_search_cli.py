import subprocess
import sys

from lantern.indexer import run_queue
from lantern.scanner.diff import scan_root
from lantern.search.basic import keyword_search, vector_search
from lantern.textnorm import fold, fts_query


def test_fold_polish_l_stroke():
    assert fold("Łódź łąka") == "Lódź ląka"


def test_fts_query_escapes_syntax():
    assert fts_query('ORA-01555 "drop" OR *') == '"ora 01555" OR "ora" OR "01555" OR "drop"'
    assert fts_query("!!") is None


def test_fts_query_drops_stopwords_and_uses_stems():
    assert fts_query("jak połączyć się z bazą danych") == '"polącz"* OR "bazą" OR "danyc"*'
    # stems keep 5+ letters for words of 6+: "drzew"* (not "drze"*, which also matched "drzwi")
    assert fts_query("optymalizacja drzewa decyzyjnego") == '"optymaliza"* OR "drzew"* OR "decyzyjn"*'
    assert fts_query("backoff_wrapper") == '"backoff_wrapper"'
    assert fts_query("jak to") == '"jak" OR "to"'  # stopword-only query still searches


def test_numbers_and_codes_with_separators_are_phrases():
    from lantern.textnorm import term_coverage

    assert fts_query("187,15 zł") == '"187 15" OR "187" OR "15" OR "zl"'
    assert fts_query("FV/2025/09/0932").startswith('"fv 2025 09 0932" OR "fv"')
    # coverage needs the parts next to each other: another invoice with "187,40" and a date with "15" does not count
    assert term_coverage("187,15 zł", "Do zapłaty 187,15 zł") == 1.0
    assert term_coverage("187,15 zł", "Energia 187,40 zł, data 15.10.2025") == 0.5


def test_keyword_search_handles_polish_l(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    hits = keyword_search(conn, "lodzi")
    assert hits and hits[0].path.endswith("łódź.txt")


def test_keyword_search_finds_exact_identifier(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    hits = keyword_search(conn, "backoff_wrapper")
    assert hits[0].path.endswith("net.py") and hits[0].symbol == "backoff_wrapper"


def test_vector_search_returns_best_chunk_per_file(conn, project, embedder):
    scan_root(conn, project)
    run_queue(conn, embedder)
    hits = vector_search(conn, embedder, "przycinanie drzewa decyzyjnego")
    assert hits[0].path.endswith("README.md")
    assert len({h.path for h in hits}) == len(hits)


def test_vector_search_on_empty_index(conn, embedder):
    assert vector_search(conn, embedder, "cokolwiek") == []


def run_cli(*args, env):
    return subprocess.run([sys.executable, "-m", "lantern.cli", *args], capture_output=True, text=True, env=env)


def test_cli_end_to_end_with_fake_embedder(project, lantern_home, monkeypatch):
    import os

    env = {**os.environ, "LANTERN_HOME": str(lantern_home), "LANTERN_EMBEDDER": "fake"}
    out = run_cli("index", str(project), env=env)
    assert out.returncode == 0, out.stderr
    assert "3 new" in out.stdout and "indexed 3 files" in out.stdout

    out = run_cli("search", "backoff_wrapper", env=env)
    assert out.returncode == 0 and "net.py" in out.stdout and "keywords #1" in out.stdout

    out = run_cli("open", "1", "--print", env=env)
    assert out.returncode == 0 and out.stdout.strip().endswith("net.py:1")

    out = run_cli("status", env=env)
    assert "files: 3 indexed=3" in out.stdout and "model: fake-bow" in out.stdout
    assert "search history: 1 searches, 1 with an opened result" in out.stdout

    out = run_cli("remove", str(project), env=env)
    assert out.returncode == 0 and "3 files" in out.stdout

    out = run_cli("index", str(project / "missing"), env=env)
    assert out.returncode == 1 and "Nothing was removed" in out.stderr

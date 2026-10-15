import subprocess
import sys

import lantern


def test_version():
    assert lantern.__version__


def test_cli_help():
    out = subprocess.run([sys.executable, "-m", "lantern.cli", "--help"], capture_output=True, text=True, check=True)
    assert "index" in out.stdout and "search" in out.stdout and "status" in out.stdout


def test_schema_tables(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"roots", "files", "chunks", "embedding_models", "jobs", "search_events", "chunks_fts"} <= tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2

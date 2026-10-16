import subprocess
import sys
from pathlib import Path

import lantern
from lantern.store import db


def test_version():
    assert lantern.__version__


def test_cli_help():
    out = subprocess.run([sys.executable, "-m", "lantern.cli", "--help"], capture_output=True, text=True, check=True)
    assert "index" in out.stdout and "search" in out.stdout and "status" in out.stdout


def test_schema_tables(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"roots", "files", "chunks", "embedding_models", "jobs", "search_events", "chunks_fts", "symbols"} <= tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_man_page_describes_every_command_and_option():
    """docs/lantern.1 is written by hand: fail when the parser gains a command or option it does not mention."""
    import argparse
    import re

    from lantern import __version__
    from lantern.cli import build_parser

    page = (Path(__file__).parent.parent / "docs" / "lantern.1").read_text()
    text = page.replace("\\-", "-").replace("\\fB", "").replace("\\fR", "").replace("\\fI", "")
    text = re.sub(r"^\.\w+ ?", "", text, flags=re.MULTILINE)  # macro names, keep their arguments
    assert f'"Lantern {__version__}"' in page, "update the version in the .TH line"
    sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    for name, parser in sub.choices.items():
        assert re.search(rf"(^|\s){re.escape(name)}(\s|$)", text, re.MULTILINE), f"command {name} missing"
        for action in parser._actions:
            for flag in action.option_strings:
                if flag not in ("-h", "--help"):
                    assert flag in text, f"option {name} {flag} missing"

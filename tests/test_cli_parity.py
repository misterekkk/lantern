"""The CLI has what the app has (models, rebuild, languages) and prints JSON where scripts need it (`--json`)."""

import json

import pytest
from conftest import write

from lantern import cli
from lantern.indexer import run_queue
from lantern.scanner.diff import scan_root
from lantern.service import machine
from lantern.store import db


@pytest.fixture
def no_daemon(monkeypatch):
    monkeypatch.setattr(cli, "_daemon", lambda: False)
    monkeypatch.setenv("LANTERN_EMBEDDER", "fake")


def _indexed(lantern_home, tmp_path):
    root = tmp_path / "docs"
    write(root, "umowa.txt", "Umowa najmu mieszkania, czynsz i kaucja.")
    conn = db.connect(lantern_home / "index.db")
    scan_root(conn, root)
    from lantern.embedding.fake import FakeEmbedder

    run_queue(conn, FakeEmbedder())
    conn.close()
    return root


def test_json_output_for_scripts(lantern_home, tmp_path, no_daemon, capsys):
    root = _indexed(lantern_home, tmp_path)
    assert cli.main(["search", "--no-log", "--json", "umowa najmu"]) == 0
    reply = json.loads(capsys.readouterr().out)
    assert reply["results"][0]["path"] == str(root / "umowa.txt") and "order" in reply
    assert cli.main(["status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["daemon"] is None and status["files"]["indexed"] == 1 and status["roots"][0]["path"] == str(root)
    assert cli.main(["config", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["index_mode"] in ("ask", "auto")


def test_models_list_and_the_memory_guard_on_download(lantern_home, no_daemon, monkeypatch, capsys):
    monkeypatch.setattr(machine, "read", lambda: machine.Memory(8 * machine.GB, 1, 50, 0))
    assert cli.main(["models", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["profile"] == "light" and {m["key"] for m in out["models"] if m["recommended"]} == {"embeddinggemma"}
    started = []
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: started.append(a) or type("R", (), {"returncode": 0}))
    assert cli.main(["models", "download", "whisper"]) == 1  # 8 GB: needs --yes
    assert "needs more memory" in capsys.readouterr().err and not started
    assert cli.main(["models", "download", "whisper", "--yes"]) == 0 and started  # the download process starts


def test_languages_add_and_remove_without_a_daemon(lantern_home, no_daemon, capsys):
    assert cli.main(["languages", "add", "de", "--json"]) == 0
    assert "de" in json.loads(capsys.readouterr().out)["extra"]
    assert cli.main(["languages", "remove", "de", "--json"]) == 0
    assert "de" not in json.loads(capsys.readouterr().out)["extra"]
    assert cli.main(["languages", "add"]) == 1


def test_rebuild_asks_first_then_keeps_the_folders(lantern_home, tmp_path, no_daemon, capsys):
    root = _indexed(lantern_home, tmp_path)
    assert cli.main(["rebuild"]) == 1 and "--yes" in capsys.readouterr().err
    assert cli.main(["rebuild", "--yes"]) == 0
    conn = db.connect(lantern_home / "index.db")
    assert [r[0] for r in conn.execute("SELECT path FROM roots")] == [str(root)]
    assert conn.execute("SELECT count(*) FROM files WHERE status = 'indexed'").fetchone()[0] == 1
    conn.close()

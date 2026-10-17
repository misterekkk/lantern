import sqlite3

from lantern import importers
from lantern.search.hybrid import learned_choice

CALC = "/System/Applications/Calculator.app"


def alfred_db(path, knowledge=(), latching=()):
    conn = sqlite3.connect(path)
    conn.executescript(  # the schema of Alfred 5.8's knowledge.alfdb
        "CREATE TABLE knowledge(item, keyword, ts decimal, hidden integer);"
        "CREATE TABLE other(type decimal, uid, data, context, ts decimal);"
        "CREATE TABLE recentdocs(path, bundle, ts decimal);"
        "CREATE TABLE latching(item, keyword, strong decimal, ts decimal);"
    )
    conn.executemany("INSERT INTO knowledge VALUES (?, ?, ?, 0)", knowledge)
    conn.executemany("INSERT INTO latching VALUES (?, ?, 1, ?)", latching)
    conn.commit()
    conn.close()
    return path


def test_alfred_choices_become_learned_choices(conn, tmp_path):
    path = alfred_db(
        tmp_path / "knowledge.alfdb",
        knowledge=[
            (CALC, "kal", 810000000.0),
            (CALC, "kalk", 810000100.0),
            ("/Applications/Gone.app", "go", 810000200.0),
        ],
    )
    assert importers.import_alfred(conn, path) == 2  # the uninstalled app is skipped
    assert learned_choice(conn, "ka") == f"app|0|-1|{CALC}|"
    assert importers.import_alfred(conn, path) == 0  # importing again adds nothing


def test_a_latched_keyword_is_learned_at_once(conn, tmp_path):
    conn.execute("INSERT INTO embedding_models(name, revision, backend, dtype, dim) VALUES ('m', 'r', 'b', 'd', 4)")
    conn.execute(
        "INSERT INTO apps(path, name, names, keywords, bundle_id, mtime, languages, document, model_id, embedding)"
        " VALUES (?, 'Kalkulator', '[]', '[]', 'com.apple.calculator', 0, 'pl', '', 1, x'')",
        (CALC,),
    )
    path = alfred_db(tmp_path / "knowledge.alfdb", latching=[("com.apple.calculator", "c", 1792000000.0)])
    assert importers.import_alfred(conn, path) == 2
    assert learned_choice(conn, "c") == f"app|0|-1|{CALC}|"  # by bundle id, Unix timestamps too


def test_no_alfred_no_import(conn, tmp_path):
    assert importers.import_alfred(conn, tmp_path / "missing.alfdb") == 0

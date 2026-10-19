"""Scoped search (`in:folder`): the token is cut from the query and keeps results to files under that folder, in the
words list, the meaning list and code definitions; apps and answers are not in a folder."""

import os

from conftest import write

from lantern.indexer import run_queue
from lantern.scanner.diff import scan_root
from lantern.search.hybrid import search
from lantern.search.scope import like_pattern, split_scope


def test_split_scope_cuts_the_token():
    assert split_scope("umowa in:Downloads najmu") == ("umowa najmu", "%/Downloads%/%")
    assert split_scope('in:"Moje dokumenty" faktura') == ("faktura", "%/Moje dokumenty%/%")
    assert split_scope("faktura") == ("faktura", None)
    assert split_scope("in:") == ("in:", None)  # nothing after the colon: an ordinary word
    q, pattern = split_scope("in:~/Projects/lantern readme")
    assert q == "readme" and pattern == os.path.expanduser("~/Projects/lantern") + "/%"
    assert like_pattern("100%_done") == "%/100\\%\\_done%/%"  # LIKE wildcards in a folder name are literal


def test_scope_keeps_results_to_the_folder(conn, embedder, tmp_path):
    root = tmp_path / "home"
    write(root, "Studia/umowa.txt", "Umowa najmu mieszkania na rok, czynsz płatny do dziesiątego.\n")
    write(root, "Downloads/umowa.txt", "Umowa najmu lokalu użytkowego, kaucja zwrotna po roku.\n")
    write(root, "Downloads/code/rent.py", "def umowa_najmu(czynsz):\n    return czynsz * 12\n")
    scan_root(conn, root)
    run_queue(conn, embedder)

    everywhere = {r.path for r in search(conn, embedder, "umowa najmu", log=False).results}
    assert any("/Studia/" in p for p in everywhere) and any("/Downloads/" in p for p in everywhere)

    scoped = search(conn, embedder, "in:down umowa najmu", log=False)
    assert scoped.results and all("/Downloads/" in r.path for r in scoped.results)
    assert all("/Downloads/" in d.path for d in scoped.definitions)
    assert not scoped.apps and not scoped.settings and scoped.answer is None

    assert not search(conn, embedder, "in:Studia umowa_najmu", log=False).definitions
    assert not search(conn, embedder, "in:nonexistent umowa", log=False).results
    assert not search(conn, embedder, "in:Downloads", log=False).results  # a scope alone is not a query


def test_large_scope_uses_a_wider_knn_pool(conn, embedder, tmp_path, monkeypatch):
    from lantern.search import scope as scope_mod

    monkeypatch.setattr(scope_mod, "EXACT_SCOPE_MAX", 0)  # every scope "large": the KNN branch
    root = tmp_path / "home"
    write(root, "a/x.txt", "Przycinanie drzewa decyzyjnego.\n")
    write(root, "b/y.txt", "Przycinanie drzewa decyzyjnego i głębokość.\n")
    scan_root(conn, root)
    run_queue(conn, embedder)
    hits = search(conn, embedder, "in:b przycinanie drzewa", log=False).results
    assert hits and all("/b/" in h.path for h in hits)


def test_vectors_read_from_vec0_blocks_match_its_own_lookups(conn, embedder, project):
    from lantern.search import basic
    from lantern.store import db

    scan_root(conn, project)
    run_queue(conn, embedder)
    table = db.vec_table(db.find_model(conn, embedder.model_info()))
    ids = [r[0] for r in conn.execute(f"SELECT chunk_id FROM {table}")]
    fast = basic._vectors_from_blocks(conn, table, ids + [999999])  # an unknown id is simply absent
    assert set(fast) == set(ids)
    for c in ids:
        own = conn.execute(f"SELECT embedding FROM {table} WHERE chunk_id = ?", (c,)).fetchone()[0]
        assert fast[c].tobytes() == own

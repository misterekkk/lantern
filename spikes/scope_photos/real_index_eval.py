"""Scoped photo search, current vs "scored against the whole library" (the people filter's mechanism applied to the
scope). Read-only on the live index; searches are not logged. Prints aggregates only (no file names)."""
import collections
import json
import os
import sqlite3
import sys
from pathlib import Path

from lantern.embedding.mlx_backend import MLXEmbedder
from lantern.embedding.siglip import SiglipQueryEmbedder
from lantern.search import basic, hybrid
from lantern.search import scope as scope_mod
from lantern.store import db

DB = Path(os.path.expanduser("~/Library/Application Support/Lantern/index.db"))
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
db.load_extensions(conn) if hasattr(db, "load_extensions") else None
try:
    import sqlite_vec

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
except Exception as e:  # noqa: BLE001
    sys.exit(f"sqlite-vec: {e}")
conn.row_factory = sqlite3.Row

emb, sq = MLXEmbedder(), SiglipQueryEmbedder()
cache_t, cache_q = {}, {}
orig_t, orig_q = emb.embed_text, sq.embed_queries
emb.embed_text = lambda texts: cache_t.setdefault(tuple(texts), orig_t(texts))
sq.embed_queries = lambda texts: cache_q.setdefault(tuple(texts), orig_q(texts))

original = basic.image_search


def library_pool(conn, embedder, query, limit=10, lang="en", query_embedder=None, scope=None, only=None):
    if scope is None:
        return original(conn, embedder, query, limit, lang, query_embedder, scope=None, only=only)
    as_scope = scope if isinstance(scope, scope_mod.Scope) else scope_mod.Scope(scope_mod.PATH_CONDITION, (scope,))
    return original(conn, embedder, query, limit, lang, query_embedder, scope=None,
                    only=scope_mod.Scope.all(as_scope, only))


folders = collections.Counter(
    os.path.dirname(r[0]) for r in conn.execute(
        "SELECT DISTINCT f.path FROM files f JOIN chunks c ON c.file_id = f.id WHERE c.kind = 'image'"))
PHOTO_Q = ["plaża", "morze", "góry", "las", "pies", "kot", "jedzenie", "samochód", "miasto nocą", "zachód słońca",
           "dom", "ludzie", "selfie", "dziecko", "kwiaty", "śnieg", "zrzut ekranu", "dokument", "tekst na kartce",
           "wykres", "mapa", "beach", "mountains", "a dog", "food", "a car", "city at night", "sunset", "people",
           "screenshot", "a document", "a chart"]
CONTROL_Q = ["def load config", "faktura vat", "sqlite vector search", "qwerty zxcv", "npm install", "import numpy"]


def shown(q, variant):
    hybrid.image_search = variant
    r = hybrid.search(conn, emb, q, log=False, lang="pl", query_embedder=sq)
    return {p.path for p in r.photos}


rows = []
for q in PHOTO_Q + CONTROL_Q:
    unscoped = shown(q, original)
    for f, n in folders.items():
        sq_ = f'in:"{f}" {q}'
        old, new = shown(sq_, original), shown(sq_, library_pool)
        in_f = {p for p in unscoped if os.path.dirname(p) == f}
        rows.append({"q": q, "control": q in CONTROL_Q, "n": n, "u": len(in_f), "old_has_u": len(in_f & old),
                     "new_has_u": len(in_f & new), "old": len(old), "new": len(new),
                     "jacc": (len(old & new) / len(old | new)) if (old | new) else 1.0})
    print(q, flush=True, file=sys.stderr)


def rate(sel, key):
    return f"{sum(1 for r in sel if r[key]) / max(1, len(sel)):.1%} ({sum(1 for r in sel if r[key])}/{len(sel)})"


out = {}
real = [r for r in rows if not r["control"]]
cons = [r for r in real if r["u"]]
out["pairs where the unscoped search shows a photo from the folder"] = len(cons)
out["...and in:folder shows it too: old"] = rate(cons, "old_has_u")
out["...and in:folder shows it too: new"] = rate(cons, "new_has_u")
for label, sel in (("small folders (<8)", [r for r in real if r["n"] < 8]), ("large folders (>=8)", [r for r in real if r["n"] >= 8])):
    out[f"{label}: in:folder shows any photo, old"] = rate(sel, "old")
    out[f"{label}: in:folder shows any photo, new"] = rate(sel, "new")
big = [r for r in real if r["n"] >= 8 and (r["old"] or r["new"])]
out["large folders: mean Jaccard old vs new (where either shows photos)"] = round(sum(r["jacc"] for r in big) / max(1, len(big)), 3)
ctrl = [r for r in rows if r["control"]]
out["control queries: in:folder shows photos, old"] = rate(ctrl, "old")
out["control queries: in:folder shows photos, new"] = rate(ctrl, "new")
out["folders"] = len(folders)
out["queries"] = f"{len(PHOTO_Q)} photo + {len(CONTROL_Q)} control"
print(json.dumps(out, ensure_ascii=False, indent=1))

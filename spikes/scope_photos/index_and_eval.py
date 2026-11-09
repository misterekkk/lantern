"""Indexes one layout (its own LANTERN_HOME) and measures three variants of scoped photo search.
    index_and_eval.py <work> <seed>"""
import collections
import json
import os
import sys
import time
from pathlib import Path

work, seed = Path(sys.argv[1]), sys.argv[2]
lay = json.loads((work / "layouts.json").read_text())[seed]
os.environ["LANTERN_HOME"] = lay["home"]
os.environ["LANTERN_NOTIFY"] = "0"
sys.path.insert(0, str(Path(__file__).parent))
from bench_data import CLASSES, CONTROL  # noqa: E402

from lantern.embedding.mlx_backend import MLXEmbedder  # noqa: E402
from lantern.embedding.siglip import SiglipImageEmbedder, SiglipQueryEmbedder  # noqa: E402
from lantern.indexer import run_queue  # noqa: E402
from lantern.scanner.diff import scan_root  # noqa: E402
from lantern.search import basic, hybrid  # noqa: E402
from lantern.search import scope as scope_mod  # noqa: E402
from lantern.store import db  # noqa: E402

home = Path(lay["home"])
conn = db.connect(home / "index.db")
emb = MLXEmbedder()
t = time.perf_counter()
scan_root(conn, home / "Zdjecia")
img = SiglipImageEmbedder()
run_queue(conn, emb, image_embedder=img)
img.unload() if hasattr(img, "unload") else None
index_s = time.perf_counter() - t
n_vec = conn.execute("SELECT count(*) FROM chunks WHERE kind = 'image'").fetchone()[0]

sq = SiglipQueryEmbedder()
cache_t, cache_q = {}, {}
orig_t, orig_q = emb.embed_text, sq.embed_queries
emb.embed_text = lambda texts: cache_t.setdefault(tuple(texts), orig_t(texts))
sq.embed_queries = lambda texts: cache_q.setdefault(tuple(texts), orig_q(texts))

original = basic.image_search


def library(conn, embedder, query, limit=10, lang="en", query_embedder=None, scope=None, only=None):
    if scope is None:
        return original(conn, embedder, query, limit, lang, query_embedder, scope=None, only=only)
    s = scope if isinstance(scope, scope_mod.Scope) else scope_mod.Scope(scope_mod.PATH_CONDITION, (scope,))
    return original(conn, embedder, query, limit, lang, query_embedder, scope=None, only=scope_mod.Scope.all(s, only))


def either(conn, embedder, query, limit=10, lang="en", query_embedder=None, scope=None, only=None):
    a = original(conn, embedder, query, limit, lang, query_embedder, scope=scope, only=only)
    if scope is None:
        return a
    best = {}
    for h in a + library(conn, embedder, query, limit, lang, query_embedder, scope=scope, only=only):
        if h.path not in best or h.score > best[h.path].score:
            best[h.path] = h
    return sorted(best.values(), key=lambda h: -h.score)[:limit]


VARIANTS = {"current": original, "library": library, "either": either}
truth = lay["truth"]
queries = [(q, c) for c, qs in CLASSES.items() for q in qs] + [(q, None) for q in CONTROL]
stats = collections.defaultdict(collections.Counter)
leaks = 0
for q, c in queries:
    for folder, n in lay["folders"]:
        size = "small" if n < 8 else "large"
        rel = {p for p, k in truth.items() if k == c and os.path.dirname(p) == folder}
        for name, fn in VARIANTS.items():
            hybrid.image_search = fn
            r = hybrid.search(conn, emb, f'in:"{folder}" {q}', log=False, lang="pl", query_embedder=sq)
            shown = {p.path for p in r.photos}
            leaks += sum(1 for p in shown if os.path.dirname(p) != folder)
            st = stats[f"{name}|{size}"]
            if rel:
                st["pairs_with_match"] += 1
                st["found_a_match"] += bool(shown & rel)
                st["matches"] += len(rel)
                st["matches_shown"] += len(shown & rel)
            else:
                st["pairs_without_match"] += 1
                st["showed_something_anyway"] += bool(shown)
            st["shown"] += len(shown)
            st["shown_correct"] += len(shown & rel)
print(json.dumps({"seed": seed, "images": n_vec, "index_s": round(index_s), "leaks": leaks,
                  "stats": {k: dict(v) for k, v in stats.items()}}))

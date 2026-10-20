"""Known-item queries on a copy of a real index: does a fusion change keep finding a file by words from it?

150 random text, PDF and code passages (seed 7); the query is 5 random words of 5+ letters from the passage, the
answer its file. Prints hit@1, hit@5, MRR per fusion (DECISIONS.md, "Fusion and inflections measured"). Run it on a
backup of the index (sqlite3 backup), never the live file:

    uv run python spikes/phase17/known_item.py <lantern home with index.db>
"""

import os
import random
import re
import sys
from pathlib import Path

from lantern.embedding.mlx_backend import MLXEmbedder
from lantern.search import basic, hybrid
from lantern.store import db

sys.path.insert(0, str(Path(__file__).parent))
from fusion_eval import variant  # noqa: E402


def main() -> int:
    home = sys.argv[1]
    os.environ["LANTERN_HOME"] = home
    conn = db.connect(Path(home) / "index.db")
    rows = conn.execute(
        "SELECT c.text, f.path FROM chunks c JOIN files f ON f.id = c.file_id "
        "WHERE c.kind IN ('text', 'code', 'pdf') AND c.text IS NOT NULL AND length(c.text) > 400 "
        "AND f.path NOT LIKE '%/spikes/%' AND f.path NOT LIKE '%/bench-%'"
    ).fetchall()
    rng = random.Random(7)
    picked = []
    for text, path in rng.sample(rows, len(rows)):
        words = sorted({w for w in re.findall(r"[^\W\d_]{5,}", text)})
        if len(words) >= 8:
            picked.append((" ".join(rng.sample(words, 5)), path))
        if len(picked) == 150:
            break
    embedder = MLXEmbedder()
    methods = {
        "dense": lambda v, kw: [h.path for h in v],
        "sparse": lambda v, kw: [h.path for h in kw],
        "RRF k=60 (old)": lambda v, kw: [r.path for r in hybrid.fuse(v, kw, 50, k=60, keyword_weight=1.0)],
        "RRF k=10 sparse 0.5": lambda v, kw: [r.path for r in hybrid.fuse(v, kw, 50, k=10, keyword_weight=0.5)],
    }
    stats = {(m, f): [0, 0, 0.0] for m in [*methods, "adaptive"] for f in ("original", "one word")}
    for query, path in picked:
        for form, q in (("original", query), ("one word", variant(query, "pl", one=True))):
            vector = [h for h in basic.vector_search(conn, embedder, q, 50) if h.kind not in ("image", "ocr")]
            keyword = [h for h in basic.keyword_search(conn, q, 50) if h.kind not in ("image", "ocr")]
            for name, fn in {**methods, "adaptive": lambda v, kw: [r.path for r in hybrid.fuse(v, kw, 50, query=q)]}.items():
                paths = list(dict.fromkeys(fn(vector, keyword)))
                rank = paths.index(path) + 1 if path in paths else None
                s = stats[(name, form)]
                s[0] += rank == 1
                s[1] += bool(rank and rank <= 5)
                s[2] += 1 / rank if rank else 0.0
    n = len(picked)
    for (name, form), (h1, h5, mrr) in stats.items():
        print(f"{name:28} {form:9} hit@1 {h1 / n:.2f}  hit@5 {h5 / n:.2f}  MRR {mrr / n:.3f}")
    if "--grid" in sys.argv:  # keyword-query fusion: tuned on even queries, reported on odd ones
        cache = [(path, [h for h in basic.vector_search(conn, embedder, q, 50) if h.kind not in ("image", "ocr")],
                  [h for h in basic.keyword_search(conn, q, 50) if h.kind not in ("image", "ocr")]) for q, path in picked]
        for k in (10, 20, 40, 60):
            for w in (1.0, 1.5, 2.0, 3.0):
                mrr = [0.0, 0.0]
                for i, (path, v, kw) in enumerate(cache):
                    paths = list(dict.fromkeys(r.path for r in hybrid.fuse(v, kw, 50, k=k, keyword_weight=w)))
                    mrr[i % 2] += 1 / (paths.index(path) + 1) if path in paths else 0.0
                print(f"grid k={k} w={w}: tune MRR {mrr[0] / (n / 2):.3f}  report MRR {mrr[1] / (n / 2):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

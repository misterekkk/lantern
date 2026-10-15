"""Scale study for whole-computer indexing: sqlite-vec brute-force KNN at 100k-1M vectors.

Variants: float[768] (current), float[256] (Matryoshka truncation, renormalised), int8[768] (vec_quantize_int8
'unit'), bit[768] (vec_quantize_binary) with float re-ranking of the top candidates.

  latency  synthetic random unit vectors: query time (warm), insert time, bytes per vector
  quality  real embeddings: overlap of each variant's top-10 with exact float[768] top-10 (no labels needed) on the
           ~/Studia/Projekt index, plus caption->photo R@1 on the Flickr test (spikes/phase7/data/eg2.npz)

Usage: uv run python spikes/scale/scale_eval.py latency [N ...] | quality <index.db>
"""

import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import sqlite_vec

HERE = Path(__file__).parent
DIM, SHORT, K, RERANK = 768, 256, 10, 200


def unit(x: np.ndarray) -> np.ndarray:
    return x / np.linalg.norm(x, axis=-1, keepdims=True)


def connect(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    return db


def latency(sizes: list[int]) -> dict:
    rng = np.random.default_rng(0)
    out = {}
    for n in sizes:
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            db = connect(Path(tmp) / "scale.db")
            db.executescript(f"""
                create virtual table f768 using vec0(e float[{DIM}] distance_metric=cosine);
                create virtual table f256 using vec0(e float[{SHORT}] distance_metric=cosine);
                create virtual table i8 using vec0(e int8[{DIM}] distance_metric=cosine);
                create virtual table b1 using vec0(e bit[{DIM}]);
            """)
            t_insert = {}
            for start in range(0, n, 20000):
                v = unit(rng.standard_normal((min(20000, n - start), DIM)).astype(np.float32))
                ids = range(start + 1, start + 1 + len(v))
                for table, rows in (
                    ("f768", ((i, x.tobytes()) for i, x in zip(ids, v, strict=True))),
                    ("f256", ((i, unit(x[:SHORT]).tobytes()) for i, x in zip(ids, v, strict=True))),
                ):
                    t0 = time.perf_counter()
                    with db:
                        db.executemany(f"insert into {table}(rowid, e) values (?, ?)", rows)
                    t_insert[table] = t_insert.get(table, 0) + time.perf_counter() - t0
                for table, fn in (("i8", "vec_quantize_int8(?, 'unit')"), ("b1", "vec_quantize_binary(?)")):
                    t0 = time.perf_counter()
                    with db:
                        db.executemany(f"insert into {table}(rowid, e) values (?, {fn})",
                                       ((i, x.tobytes()) for i, x in zip(ids, v, strict=True)))  # fmt: skip
                    t_insert[table] = t_insert.get(table, 0) + time.perf_counter() - t0
            queries = unit(rng.standard_normal((12, DIM)).astype(np.float32))

            def timed(fn):
                fn(queries[0])  # warm the page cache
                ts = []
                for q in queries[1:]:
                    t0 = time.perf_counter()
                    fn(q)
                    ts.append((time.perf_counter() - t0) * 1000)
                return round(float(np.median(ts)), 1)

            def knn(table, expr, q, k):
                return db.execute(f"select rowid from {table} where e match {expr} and k = ?", (q.tobytes(), k)).fetchall()

            def bit_rerank(q):
                cand = [r[0] for r in knn("b1", "vec_quantize_binary(?)", q, RERANK)]
                marks = ",".join("?" * len(cand))
                rows = db.execute(f"select rowid, e from f768 where rowid in ({marks})", cand).fetchall()
                vecs = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), DIM)
                return np.argsort(-(vecs @ q))[:K]

            res = {
                "f768_ms": timed(lambda q: knn("f768", "?", q, K)),
                "f256_ms": timed(lambda q: knn("f256", "?", unit(q[:SHORT]), K)),
                "int8_ms": timed(lambda q: knn("i8", "vec_quantize_int8(?, 'unit')", q, K)),
                "bit+rerank_ms": timed(bit_rerank),
                "insert_s": {k: round(v, 1) for k, v in t_insert.items()},
                "db_mb": round((Path(tmp) / "scale.db").stat().st_size / 2**20),
            }
            db.close()
        out[n] = res
        print(n, json.dumps(res), flush=True)
    return out


def variants(docs: np.ndarray, queries: np.ndarray) -> dict[str, np.ndarray]:
    """Score matrices [queries, docs] for each variant (numpy model of sqlite-vec's quantisers)."""
    exact = queries @ docs.T
    short = unit(queries[:, :SHORT]) @ unit(docs[:, :SHORT]).T
    q8, d8 = np.clip(np.round(queries * 127), -128, 127), np.clip(np.round(docs * 127), -128, 127)
    int8 = unit(q8) @ unit(d8).T
    qb, db_ = queries > 0, docs > 0
    hamming = (qb[:, None, :] != db_[None, :, :]).sum(-1)
    bit = np.full_like(exact, -np.inf)
    for i in range(len(queries)):  # binary candidates, float re-ranking
        cand = np.argsort(hamming[i])[:RERANK]
        bit[i, cand] = exact[i, cand]
    return {"f768": exact, "f256": short, "int8": int8, "bit+rerank": bit}


def overlap(scores: dict[str, np.ndarray]) -> dict[str, float]:
    ref = np.argsort(-scores["f768"], axis=1)[:, :K]
    out = {}
    for name, s in scores.items():
        top = np.argsort(-s, axis=1)[:, :K]
        out[name] = round(float(np.mean([len(set(a) & set(b)) / K for a, b in zip(ref, top, strict=True)])), 3)
    return out


def quality(index_db: str) -> dict:
    from lantern.embedding.base import format_query
    from lantern.embedding.mlx_backend import MLXEmbedder

    db = connect(Path(index_db))
    model_id = db.execute("select id from embedding_models where backend = 'mlx'").fetchone()[0]
    rows = db.execute(
        f"select v.embedding, c.text from vec_chunks_{model_id} v join chunks c on c.id = v.chunk_id"
    ).fetchall()
    docs = np.frombuffer(b"".join(r[0] for r in rows), dtype=np.float32).reshape(len(rows), DIM)
    rng = np.random.default_rng(1)
    texts = [r[1] for r in rows if r[1]]
    picks = [texts[i] for i in rng.choice(len(texts), 300, replace=False)]
    query_texts = [" ".join(t.split()[:8]) for t in picks]  # short, query-like
    queries = MLXEmbedder().embed_text([format_query(q) for q in query_texts])
    out = {"projects_overlap@10": overlap(variants(docs, queries)), "projects_chunks": len(docs)}

    z = np.load(HERE.parent / "phase7" / "data" / "eg2.npz")
    img, txt, names = z["img"], z["txt"], list(z["queries"])
    sys.path.insert(0, str(HERE.parent / "phase7"))
    import image_eval

    data = image_eval.rows()
    caps = [(i, c) for i, r in enumerate(data) for c in r["captions"]]
    qv = np.stack([txt[names.index(c)] for _, c in caps])
    target = np.array([i for i, _ in caps])
    sc = variants(img, qv)
    out["flickr_overlap@10"] = overlap(sc)
    out["flickr_caption_R@1"] = {k: round(float((np.argmax(s, axis=1) == target).mean()), 3) for k, s in sc.items()}
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    if sys.argv[1] == "latency":
        result = latency([int(x) for x in sys.argv[2:]] or [100_000, 300_000, 1_000_000])
    else:
        result = quality(sys.argv[2])
    (HERE / f"results_{sys.argv[1]}.json").write_text(json.dumps(result, indent=1))

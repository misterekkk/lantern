"""Saves the raw candidate lists of every query (meaning and words, top 50 files each, with scores and keyword
coverage), so fusion_analyze.py can compare any number of fusions in seconds instead of searching again.

Also stores, for every word of the queries, the share of the index's passages that contain it (FTS5 vocabulary): a
word in a large share of passages is a function word of that index's language, whatever the language.

    collect_runs.py beir <home> <queries.jsonl[.gz]> <qrels dir> <lang> <out.json>
    collect_runs.py known <home> <out.json>        # 150 known-item queries from a real index (as known_item.py)
"""

import gzip
import json
import os
import random
import re
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fusion_eval import load_qrels, variant  # noqa: E402

from lantern.embedding.mlx_backend import MLXEmbedder  # noqa: E402
from lantern.search import basic  # noqa: E402
from lantern.store import db  # noqa: E402
from lantern.textnorm import fold  # noqa: E402

N = 50
PHOTO = ("image", "ocr")


def token_key(word: str) -> str:
    """As the FTS index stores it: folded, lower case, without diacritics (unicode61 remove_diacritics 2)."""
    w = unicodedata.normalize("NFKD", fold(word).lower())
    return "".join(c for c in w if not unicodedata.combining(c))


def doc_shares(conn, words: set[str]) -> tuple[int, dict[str, float]]:
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.vocab USING fts5vocab(main, chunks_fts, 'row')")
    total = conn.execute("SELECT count(*) FROM chunks_fts").fetchone()[0]
    shares = {}
    for w in words:
        row = conn.execute("SELECT doc FROM temp.vocab WHERE term = ?", (w,)).fetchone()
        shares[w] = (row[0] / total) if row else 0.0
    return total, shares


TEXTS: dict[int, str] = {}  # chunk id -> its text (once per chunk), for the reranker test


def search(conn, embedder, q, stem=True):
    t0 = time.perf_counter()
    v = [h for h in basic.vector_search(conn, embedder, q, N) if h.kind not in PHOTO]
    t1 = time.perf_counter()
    k = [h for h in basic.keyword_search(conn, q, N) if h.kind not in PHOTO]
    t2 = time.perf_counter()
    name = (lambda p: Path(p).stem) if stem else (lambda p: p)
    for h in (*v, *k):
        TEXTS.setdefault(h.chunk_id, (h.full_text or h.preview or "")[:2000])
    return ([[name(h.path), round(h.score, 5), h.chunk_id] for h in v],
            [[name(h.path), round(h.score, 5), round(h.coverage, 3), h.chunk_id] for h in k],
            round((t1 - t0) * 1000, 1), round((t2 - t1) * 1000, 1))


def main() -> int:
    mode, home = sys.argv[1], sys.argv[2]
    os.environ["LANTERN_HOME"] = home
    conn = db.connect(Path(home) / "index.db")
    embedder = MLXEmbedder()
    items = []  # (split, qid, text, rel, lang)
    if mode == "beir":
        qpath, qdir, lang, out = sys.argv[3], Path(sys.argv[4]), sys.argv[5], sys.argv[6]
        with (gzip.open if qpath.endswith(".gz") else open)(qpath, "rt", encoding="utf-8") as f:
            queries = {(r := json.loads(line))["_id"]: r["text"] for line in f}
        for split in ("train", "test"):
            if (qdir / f"{split}.tsv").exists():
                items += [(split, q, queries[q], rel, lang) for q, rel in load_qrels(qdir / f"{split}.tsv").items()
                          if q in queries]  # fmt: skip
    else:
        out, lang = sys.argv[3], "pl"
        rows = conn.execute(
            "SELECT c.text, f.path FROM chunks c JOIN files f ON f.id = c.file_id "
            "WHERE c.kind IN ('text', 'code', 'pdf') AND c.text IS NOT NULL AND length(c.text) > 400 "
            "AND f.path NOT LIKE '%/spikes/%' AND f.path NOT LIKE '%/bench-%'").fetchall()  # fmt: skip
        rng = random.Random(7)
        for text, path in rng.sample(rows, len(rows)):
            words = sorted({w for w in re.findall(r"[^\W\d_]{5,}", text)})
            if len(words) >= 8:
                items.append(("tune" if len(items) % 2 == 0 else "test", str(len(items)), " ".join(rng.sample(words, 5)),
                              {path: 1}, lang))  # fmt: skip
            if len(items) == 150:
                break
    records = []
    for split, qid, text, rel, lang in items:
        for form, q in (("original", text), ("one word", variant(text, lang, one=True)),
                        ("every word", variant(text, lang))):  # fmt: skip
            v, k, vms, kms = search(conn, embedder, q, stem=(mode == "beir"))
            records.append({"split": split, "qid": qid, "form": form, "q": q, "rel": rel, "v": v, "k": k,
                            "vms": vms, "kms": kms})  # fmt: skip
    words = {token_key(w) for r in records for w in re.findall(r"[^\W\d_]+", r["q"])}
    total, shares = doc_shares(conn, words)
    Path(out).write_text(json.dumps({"mode": mode, "lang": lang, "passages": total, "shares": shares,
                                     "texts": TEXTS, "records": records}, ensure_ascii=False))  # fmt: skip
    print(out, len(records), "searches", total, "passages")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""How well Lantern finds documents when a query changes form, and which fusion of meaning (dense) and words (sparse)
ranks best. Public data: SciFact from BEIR (English) and BEIR-PL (Polish, CLARIN), 5 183 abstracts, queries with
relevance judgements; train queries tune, test queries report (DECISIONS.md, "Fusion and inflections measured").

Every query is also asked in another form: each content word inflected differently (English: singular/plural and verb
endings; Polish: case and number endings), with the same rules for every query, so the drop shows how much a change of
form costs each method.

    uv run python spikes/phase17/fusion_eval.py <lantern home with the indexed corpus> <queries.jsonl[.gz]> <qrels dir>
"""

import gzip
import json
import math
import re
import sys
from pathlib import Path

from lantern.embedding.mlx_backend import MLXEmbedder
from lantern.search import basic, hybrid
from lantern.store import db

N = 50  # candidates per list, as hybrid.CANDIDATES
STOP = {"the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "is", "are", "with", "by", "at", "as", "be",
        "w", "z", "na", "i", "do", "o", "nie", "się", "jest", "są", "od", "przez", "oraz", "ze", "we", "dla", "po"}  # fmt: skip


def en_variant(word: str) -> str:
    w = word.lower()
    if len(w) <= 3 or w in STOP or not w.isalpha():
        return word
    if w.endswith("ies"):
        return word[:-3] + "y"
    if w.endswith("ses") or w.endswith("xes"):
        return word[:-2]
    if w.endswith("s") and not w.endswith("ss"):
        return word[:-1]
    if w.endswith("ed"):
        return word[:-2] + "ing" if not w.endswith("eed") else word
    if w.endswith("ing"):
        return word[:-3] + "ed"
    if w.endswith("y") and w[-2] not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


# Polish: one ending swapped for another of the same word (case or number), longest ending first
PL_SWAPS = [("ości", "ość"), ("ość", "ości"), ("owych", "owe"), ("owe", "owych"), ("ach", "ami"), ("ami", "ach"),
            ("ych", "ym"), ("ym", "ych"), ("ego", "emu"), ("emu", "ego"), ("nia", "niu"), ("nie", "nia"),
            ("niu", "nia"), ("ów", "y"), ("ie", "a"), ("ą", "a"), ("ę", "a"), ("y", "ów"), ("a", "y"), ("e", "a"),
            ("i", "e"), ("u", "a"), ("o", "a")]  # fmt: skip


def pl_variant(word: str) -> str:
    w = word.lower()
    if len(w) <= 3 or w in STOP or not w.isalpha():
        return word
    for old, new in PL_SWAPS:
        if w.endswith(old) and len(w) - len(old) >= 3:
            return word[: len(word) - len(old)] + new
    return word + "y"


def variant(query: str, lang: str, one: bool = False) -> str:
    """Every word in another form, or only the longest one (one=True: "two men" for "two man")."""
    fn = pl_variant if lang == "pl" else en_variant
    if not one:
        return re.sub(r"[^\W\d_]+", lambda m: fn(m.group(0)), query)
    words = [m for m in re.finditer(r"[^\W\d_]+", query) if fn(m.group(0)) != m.group(0)]
    if not words:
        return query
    m = max(words, key=lambda m: len(m.group(0)))
    return query[: m.start()] + fn(m.group(0)) + query[m.end() :]


def ndcg10(ranked: list[str], rel: dict[str, int]) -> float:
    dcg = sum(rel.get(d, 0) / math.log2(i + 2) for i, d in enumerate(ranked[:10]))
    ideal = sum(r / math.log2(i + 2) for i, r in enumerate(sorted(rel.values(), reverse=True)[:10]))
    return dcg / ideal if ideal else 0.0


def recall(ranked: list[str], rel: dict[str, int], k: int) -> float:
    return len(set(ranked[:k]) & set(rel)) / len(rel)


def doc(path: str) -> str:
    return Path(path).stem


def fused(vector, keyword, k=60.0, w_dense=1.0, w_sparse=1.0, coverage=True) -> list[str]:
    """RRF over files; coverage=True weighs a keyword hit by the share of query words it contains (Lantern's way)."""
    vr = {h.path: i + 1 for i, h in enumerate(vector)}
    kr = {h.path: (i + 1, h) for i, h in enumerate(keyword)}
    scores = {}
    for p in vr.keys() | kr.keys():
        s = w_dense / (k + vr[p]) if p in vr else 0.0
        if p in kr:
            rank, h = kr[p]
            weight = (h.coverage if h.coverage >= hybrid.MIN_KEYWORD_COVERAGE else 0.0) if coverage else 1.0
            s += w_sparse * weight / (k + rank)
        if s:
            scores[p] = (s, vr.get(p, 10**6))
    return [doc(p) for p, _ in sorted(scores.items(), key=lambda kv: (-kv[1][0], kv[1][1]))]


def load_queries(path: str) -> dict[str, str]:
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as f:
        return {(r := json.loads(line))["_id"]: r["text"] for line in f}


def load_qrels(path: Path) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for line in path.read_text().splitlines()[1:]:
        q, d, s = line.split("\t")
        if int(s) > 0:
            out.setdefault(q, {})[d] = int(s)
    return out


def main() -> int:
    import os

    home, queries_path, qrels_dir, lang = sys.argv[1], sys.argv[2], Path(sys.argv[3]), sys.argv[4]
    os.environ["LANTERN_HOME"] = home
    conn = db.connect(Path(home) / "index.db")
    queries = load_queries(queries_path)
    embedder = MLXEmbedder()
    runs = {}
    splits = [s for s in ("train", "test") if (qrels_dir / f"{s}.tsv").exists()]
    for split in splits:
        qrels = load_qrels(qrels_dir / f"{split}.tsv")
        for qid, rel in qrels.items():
            text = queries.get(qid)
            if not text:
                continue
            for form, q in (("original", text), ("one word", variant(text, lang, one=True)),
                            ("every word", variant(text, lang))):
                vector = basic.vector_search(conn, embedder, q, N)
                keyword = basic.keyword_search(conn, q, N)
                runs[(split, qid, form)] = (vector, keyword, rel, q)
    examples = [(runs[k][3], runs[(k[0], k[1], "one word")][3], runs[(k[0], k[1], "every word")][3])
                for k in list(runs)[:9:3]]
    print(json.dumps({"lang": lang, "examples": examples}, ensure_ascii=False))
    from lantern.textnorm import STOPWORDS

    def sentence(q):
        return any(w in STOPWORDS for w in re.findall(r"[^\W\d_]+", q.lower()))

    methods = {
        "dense": lambda v, kw, q: [doc(h.path) for h in v],
        "sparse (BM25)": lambda v, kw, q: [doc(h.path) for h in kw],
        "RRF k=60 w=1 (old)": lambda v, kw, q: [doc(r.path) for r in hybrid.fuse(v, kw, N, k=60, keyword_weight=1.0)],
        "RRF k=10 w=0.5": lambda v, kw, q: [doc(r.path) for r in hybrid.fuse(v, kw, N, k=10, keyword_weight=0.5)],
        "adaptive (Lantern)": lambda v, kw, q: [doc(r.path) for r in hybrid.fuse(v, kw, N, query=q)],
    }
    share = sum(sentence(runs[key][3]) for key in runs if key[2] == "original") / max(1, len(runs) // 3)
    print(json.dumps({"lang": lang, "sentence_share": round(share, 3)}))
    results = {}
    per_query: dict[tuple[str, str], list[float]] = {}
    for name, fn in methods.items():
        for split in splits:
            for form in ("original", "one word", "every word"):
                keys = [key for key in runs if key[0] == split and key[2] == form]
                scores = [ndcg10(fn(runs[key][0], runs[key][1], runs[key][3]), runs[key][2]) for key in keys]
                if split == "test":
                    per_query[(name, form)] = scores
                nd = sum(scores) / len(keys)
                r10 = sum(recall(fn(runs[key][0], runs[key][1], runs[key][3]), runs[key][2], 10)
                          for key in keys) / len(keys)  # fmt: skip
                results.setdefault(name, {})[f"{split} {form}"] = (round(nd, 4), round(r10, 4), len(keys))
    print(json.dumps({"lang": lang, "results": results}, ensure_ascii=False))
    # paired bootstrap on the test queries: the new fusion against the old one, same queries resampled
    import random

    rng = random.Random(7)
    sig = {}
    for form in ("original", "one word", "every word"):
        new, old = per_query[("adaptive (Lantern)", form)], per_query[("RRF k=60 w=1 (old)", form)]
        diffs = [a - b for a, b in zip(new, old, strict=True)]
        means = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(2000))
        sig[form] = {"mean_diff": round(sum(diffs) / len(diffs), 4), "ci95": [round(means[50], 4), round(means[1949], 4)],
                     "p_not_better": round(sum(m <= 0 for m in means) / len(means), 4)}
    print(json.dumps({"lang": lang, "significance": sig}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

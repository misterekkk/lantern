"""Compares fusions of meaning and words on the lists saved by collect_runs.py (DECISIONS.md, "Fusion without
language lists").

Fusions: RRF (rank based) and convex combination (CC: a weighted sum of the two scores, each min-max normalised over
its own top 50). Query types, so that a sentence and bare keywords may be fused differently:
  none   one fusion for every query (no language knowledge at all)
  stop   Lantern's Polish and English function-word lists (the fusion shipped today)
  df>=t  a word found in at least a share t of the index's own passages counts as a function word (any language)
Tuning: sentence-type queries on SciFact train (EN + PL), keyword-type queries on the tune half of the known-item
queries; everything else only reports. nDCG@10 for BEIR sets, MRR for the known-item queries.

    fusion_analyze.py <runs dir> [<rerank dir> ...]
"""

import json
import math
import random
import re
import sys
import time
import unicodedata
from pathlib import Path

from lantern.textnorm import STOPWORDS, fold

TUNE_SETS = ("scifact-en", "scifact-pl")
REPORT_SETS = ("scifact-en", "scifact-pl", "nfcorpus-en", "nfcorpus-pl", "quora-en", "quora-pl", "fiqa-en", "fiqa-pl")
MIN_COV = 0.5


def key(w):
    w = unicodedata.normalize("NFKD", fold(w).lower())
    return "".join(c for c in w if not unicodedata.combining(c))


def rrf(r, k, w):
    s = {}
    for i, (d, *_) in enumerate(r["v"]):
        s[d] = s.get(d, 0.0) + 1.0 / (k + i + 1)
    for i, (d, _, cov, *_) in enumerate(r["k"]):
        if cov >= MIN_COV:
            s[d] = s.get(d, 0.0) + w * cov / (k + i + 1)
    return sorted(s, key=lambda d: -s[d])


def cc(r, alpha):
    s = {}
    if r["v"]:
        vs = [x for _, x, *_ in r["v"]]
        lo, hi = min(vs), max(vs)
        for d, x, *_ in r["v"]:
            s[d] = alpha * ((x - lo) / (hi - lo) if hi > lo else 1.0)
    if r["k"]:
        ks = [x for _, x, *_ in r["k"]]
        lo, hi = min(ks), max(ks)
        for d, x, cov, *_ in r["k"]:
            if cov >= MIN_COV:
                s[d] = s.get(d, 0.0) + (1 - alpha) * cov * ((x - lo) / (hi - lo) if hi > lo else 1.0)
    return sorted(s, key=lambda d: -s[d])


def ndcg(ranked, rel):
    dcg = sum(rel.get(d, 0) / math.log2(i + 2) for i, d in enumerate(ranked[:10]))
    ideal = sum(x / math.log2(i + 2) for i, x in enumerate(sorted(rel.values(), reverse=True)[:10]))
    return dcg / ideal if ideal else 0.0


def mrr(ranked, rel):
    for i, d in enumerate(ranked[:50]):
        if d in rel:
            return 1.0 / (i + 1)
    return 0.0


def detector(name, shares):
    if name == "none":
        return lambda q: "all"
    if name == "stop":
        return lambda q: "sent" if any(w in STOPWORDS for w in re.findall(r"[^\W\d_]+", q.lower())) else "kw"
    t = float(name.split(">=")[1])
    return lambda q: "sent" if any(shares.get(key(w), 0.0) >= t for w in re.findall(r"[^\W\d_]+", q)) else "kw"


RRF_GRID = [(k, w) for k in (5, 10, 20, 40, 60) for w in (0.25, 0.5, 1.0, 2.0, 3.0)]
CC_GRID = [round(a / 10, 1) for a in range(11)]


def score(fn, r):
    return mrr(fn(r), r["rel"]) if r["metric"] == "mrr" else ndcg(fn(r), r["rel"])


def main():
    runs = Path(sys.argv[1])
    data = {}
    for p in runs.glob("*.json"):
        d = json.loads(p.read_text())
        for r in d["records"]:
            r["metric"] = "mrr" if d["mode"] == "known" else "ndcg"
            r["set"] = p.stem
        data[p.stem] = d
    out = {"sets": {s: len([r for r in d["records"] if r["form"] == "original"]) for s, d in data.items()}}
    # speed of the searches themselves (milliseconds, medians)
    lat = {}
    for s, d in data.items():
        vm = sorted(r["vms"] for r in d["records"])
        km = sorted(r["kms"] for r in d["records"])
        lat[s] = {"meaning_ms": vm[len(vm) // 2], "words_ms": km[len(km) // 2], "passages": d["passages"]}
    out["latency"] = lat
    detectors = ["none", "stop", "df>=0.02", "df>=0.05", "df>=0.1", "df>=0.2"]
    results = {}
    for det in detectors:
        dets = {s: detector(det, d["shares"]) for s, d in data.items()}
        for r in (r for d in data.values() for r in d["records"]):
            r["type"] = dets[r["set"]](r["q"])
        tune = [r for r in (r for s in TUNE_SETS for r in data[s]["records"]) if r["split"] == "train"]
        tune += [r for r in data["known"]["records"] if r["split"] == "tune"]
        for family, grid, make in (("rrf", RRF_GRID, lambda p: (lambda r: rrf(r, *p))),
                                   ("cc", CC_GRID, lambda p: (lambda r: cc(r, p)))):  # fmt: skip
            best = {}
            for t in {r["type"] for r in tune}:
                part = [r for r in tune if r["type"] == t]
                if part:
                    best[t] = max(grid, key=lambda p: sum(score(make(p), r) for r in part) / len(part))
            fallback = next(iter(best.values()))
            fn = lambda r, best=best, make=make, fallback=fallback: make(best.get(r["type"], fallback))(r)  # noqa: E731
            results[f"{family} {det}"] = {"params": {t: p for t, p in best.items()}, "fn": fn}
    results["rrf old k=60 w=1"] = {"params": {}, "fn": lambda r: rrf(r, 60, 1.0)}
    stop = detector("stop", {})

    def shipped_fn(r):
        return rrf(r, 10, 0.5) if stop(r["q"]) == "sent" else rrf(r, 20, 2.0)

    results["shipped (stop, rrf 10/0.5 and 20/2)"] = {"params": {}, "fn": shipped_fn}
    for rdir in sys.argv[2:]:  # reranked top 20 of the shipped fusion (rerank_runs.py); "every word" was not reranked
        rd = Path(rdir)
        orders = {(p.stem, x["qid"], x["form"]): x["order"] for p in rd.glob("*.json") for x in json.loads(p.read_text())}
        name = f"shipped + rerank {rd.name.removeprefix('rerank-')}"
        results[name] = {"params": {}, "fn": lambda r, o=orders: o.get((r["set"], r["qid"], r["form"])) or shipped_fn(r)}
        out.setdefault("rerank_cost", {})[name] = json.loads((rd / "summary.txt").read_text())
    results["dense only"] = {"params": {}, "fn": lambda r: [x[0] for x in r["v"]]}
    results["words only"] = {"params": {}, "fn": lambda r: [x[0] for x in r["k"]]}

    def report_rows(name):
        rows = []
        for s in REPORT_SETS + ("known",):
            for form in ("original", "one word", "every word"):
                rows += [r for r in data[s]["records"] if r["form"] == form and r["split"] in ("test",)]
        return rows

    table = {}
    old_fn = results["rrf old k=60 w=1"]["fn"]
    rng = random.Random(7)
    for name, m in results.items():
        row = {"params": {k: str(v) for k, v in m["params"].items()}}
        t0 = time.perf_counter()
        n = 0
        for s in REPORT_SETS + ("known",):
            for form in ("original", "one word", "every word"):
                recs = [r for r in data[s]["records"] if r["form"] == form and r["split"] == "test"]
                vals = [score(m["fn"], r) for r in recs]
                n += len(recs)
                row[f"{s}|{form}"] = round(sum(vals) / len(vals), 4)
                if form == "original":
                    for ref, ref_fn in (("old", old_fn), ("shipped", shipped_fn)):
                        diffs = [a - score(ref_fn, r) for a, r in zip(vals, recs, strict=True)]
                        means = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(500))
                        row[f"{s}|vs {ref}"] = [round(sum(diffs) / len(diffs), 4), round(means[12], 4), round(means[487], 4)]
        row["fusion_us_per_query"] = round((time.perf_counter() - t0) / max(1, n) * 1e6, 1)
        table[name] = row
    out["table"] = table
    # how the language-free detector agrees with the function-word lists on the test queries
    agree = {}
    for det in detectors[2:]:
        dets = {s: detector(det, d["shares"]) for s, d in data.items()}
        recs = [r for d in data.values() for r in d["records"] if r["form"] == "original"]
        agree[det] = round(sum(dets[r["set"]](r["q"]) == stop(r["q"]) for r in recs) / len(recs), 3)
    out["agreement_with_lists"] = agree
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

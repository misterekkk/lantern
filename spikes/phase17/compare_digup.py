"""Lantern (the fusion on main) against DigUp on the same test queries: nDCG@10 per set and query form, a paired
bootstrap of the difference, search times and memory (DECISIONS.md, "Lantern and DigUp compared").

    python3 compare_digup.py <scratch folder with runs/, stopwords.json and dg-<set>.results.json>
"""

import json
import math
import random
import re
import sys
from pathlib import Path

SETS = ["scifact-en", "scifact-pl", "nfcorpus-en", "nfcorpus-pl", "quora-en", "quora-pl", "fiqa-en", "fiqa-pl"]


def ndcg(order, rel):
    dcg = sum(rel.get(d, 0) / math.log2(i + 2) for i, d in enumerate(order[:10]))
    ideal = sum(v / math.log2(i + 2) for i, v in enumerate(sorted(rel.values(), reverse=True)[:10]))
    return dcg / ideal if ideal else 0.0


def lantern(r, stop):
    sentence = any(w in stop for w in re.findall(r"[^\W\d_]+", r["q"].lower()))
    k, w = (10, 0.5) if sentence else (20, 2.0)
    s = {}
    for i, (d, *_) in enumerate(r["v"]):
        s[d] = s.get(d, 0.0) + 1 / (k + i + 1)
    for i, (d, _, cov, *_) in enumerate(r["k"]):
        if cov >= 0.5:
            s[d] = s.get(d, 0.0) + w * cov / (k + i + 1)
    return sorted(s, key=lambda d: -s[d])


def main():
    sp = Path(sys.argv[1])
    stop = set(json.loads((sp / "stopwords.json").read_text()))
    rng = random.Random(7)
    rows = []
    for name in SETS:
        runs = json.loads((sp / "runs" / f"{name}.json").read_text())
        dg_path = sp / f"dg-{name}.results.json"
        if not dg_path.exists():
            continue
        dg = json.loads(dg_path.read_text())
        dg_order = {(x["qid"], x["form"]): x["order"] for x in dg["results"]}
        row = {"set": name, "digup_footprint_mb": dg["footprint_mb"]}
        for form in ("original", "one word", "every word"):
            recs = [r for r in runs["records"] if r["split"] == "test" and r["form"] == form]
            a = [ndcg(lantern(r, stop), r["rel"]) for r in recs]
            b = [ndcg(dg_order.get((r["qid"], r["form"]), []), r["rel"]) for r in recs]
            diffs = [x - y for x, y in zip(a, b, strict=True)]
            means = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(1000))
            row[form] = {"lantern": round(sum(a) / len(a), 4), "digup": round(sum(b) / len(b), 4),
                         "diff": round(sum(diffs) / len(diffs), 4), "ci95": [round(means[25], 4), round(means[974], 4)],
                         "n": len(recs)}
        lt = sorted(r["vms"] + r["kms"] for r in runs["records"] if r["split"] == "test")
        dt = sorted(x["wall_ms"] for x in dg["results"])
        row["lantern_ms_median"] = round(lt[len(lt) // 2], 1)
        row["digup_ms_median"] = round(dt[len(dt) // 2], 1)
        rows.append(row)
    print(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()

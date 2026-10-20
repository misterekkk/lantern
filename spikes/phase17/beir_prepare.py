"""Turns a BEIR / BEIR-PL dataset into a folder Lantern can index plus its queries and judgements, for fusion_eval.py.

Large corpora are sampled: every document judged relevant for the chosen queries, plus random other documents up to
--max-docs (seed 7), so each query still has its answers among plenty of distractors. Needs pyarrow for the English
parquet files (run with `uv run --no-project --with pyarrow==21.0.0 python -I ...`); nothing from Lantern.

    beir_prepare.py <dataset dir> <out dir> [--max-docs 10000] [--max-queries 500]

<dataset dir> holds corpus + queries (BeIR: corpus/*.parquet, queries/*.parquet; BEIR-PL: corpus.jsonl.gz,
queries.jsonl.gz) and a sibling "<name>-qrels" folder with test.tsv (and train.tsv / dev.tsv when the set has them).
"""

import argparse
import gzip
import json
import random
import re
from pathlib import Path


def rows(path: Path):
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq

        yield from pq.read_table(path).to_pylist()
        return
    with (gzip.open if path.suffix == ".gz" else open)(path, "rt", encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def find(base: Path, stem: str) -> Path:
    for pattern in (f"{stem}/*.parquet", f"{stem}.jsonl.gz", f"{stem}.jsonl"):
        hits = sorted(base.glob(pattern))
        if hits:
            return hits[0]
    raise FileNotFoundError(f"{stem} in {base}")


def qrels(path: Path) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for line in path.read_text().splitlines()[1:]:
        q, d, s = line.split("\t")[:3]
        if int(s) > 0:
            out.setdefault(q, {})[d] = int(s)
    return out


def safe(doc_id: str) -> str:
    """The file name of a document (and its id in the judgements written here)."""
    return re.sub(r"[^\w.-]", "_", doc_id)[:80]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--max-docs", type=int, default=10_000)
    ap.add_argument("--max-queries", type=int, default=500)
    args = ap.parse_args()
    rng = random.Random(7)
    qdir = args.dataset.with_name(args.dataset.name + "-qrels")
    judged = qrels(qdir / "test.tsv")
    chosen = sorted(judged)
    if len(chosen) > args.max_queries:
        chosen = sorted(rng.sample(chosen, args.max_queries))
    needed = {d for q in chosen for d in judged[q]}
    corpus = {str(r["_id"]): r for r in rows(find(args.dataset, "corpus"))}
    others = sorted(set(corpus) - needed)
    rng.shuffle(others)
    keep = needed | set(others[: max(0, args.max_docs - len(needed))])
    docs = args.out / "corpus"
    docs.mkdir(parents=True, exist_ok=True)
    for did in keep:
        r = corpus[did]
        title = (r.get("title") or "").strip()
        name = safe(did)
        (docs / f"{name}.md").write_text((f"# {title}\n\n" if title else "") + (r.get("text") or "") + "\n")
    queries = {str(r["_id"]): r["text"] for r in rows(find(args.dataset, "queries"))}
    with open(args.out / "queries.jsonl", "w", encoding="utf-8") as f:
        for q in chosen:
            f.write(json.dumps({"_id": q, "text": queries[q]}, ensure_ascii=False) + "\n")
    (args.out / "qrels").mkdir(exist_ok=True)
    lines = ["query-id\tcorpus-id\tscore"] + [
        f"{q}\t{safe(d)}\t{s}" for q in chosen for d, s in judged[q].items() if d in keep]
    (args.out / "qrels" / "test.tsv").write_text("\n".join(lines) + "\n")
    print(json.dumps({"out": str(args.out), "docs": len(keep), "queries": len(chosen), "judged_docs": len(needed)}))


if __name__ == "__main__":
    main()

"""Phase 0 spike: can a single spoken word ("krowa") find the recording?

16 recordings (13 corpus + 3 with one incidental keyword). Run with .venv-mlx
(MLX 8-bit). Methods: audio 30 s windows, transcript vector, transcript BM25
(exact tokens and naive prefix "stem*").
"""

import json
import os
import re
import sqlite3
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ["HF_HUB_OFFLINE"] = "1"

import mlx.core as mx  # noqa: E402
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from huggingface_hub import snapshot_download  # noqa: E402
from mlx_vlm.embedding_loader import load_embedding_model  # noqa: E402
from transformers import AutoProcessor  # noqa: E402

SR = 16000
DATA = Path(__file__).parent / "data"
model = load_embedding_model(Path(snapshot_download("mlx-community/embeddinggemma-2-8bit")))
proc = AutoProcessor.from_pretrained("google/embeddinggemma-2", revision="914f7f89142e33e77833254d9c9b90c3cef7303b")


def run(inputs):
    e = model(**{k: mx.array(v) for k, v in inputs.items()}).text_embeds
    mx.eval(e)
    return np.array(e.astype(mx.float32))


def texts(t):
    return run(proc.tokenizer(t, padding=True, return_tensors="np"))


def audio(w):
    return run(proc(text="<|audio|>", audio=[w], sampling_rate=SR, return_tensors="np",
                    audio_kwargs={"max_length": 30 * SR, "truncation": True}))[0]


recs = {}
for p in sorted((DATA / "corpus").glob("*.wav")) + sorted((DATA / "keyword").glob("*.wav")):
    recs[p.stem] = sf.read(p, dtype="float32")[0]
tr = json.loads((DATA / "corpus" / "transcripts.json").read_text())
tr = {k: v["text"] for k, v in tr.items()} | json.loads((DATA / "keyword" / "transcripts.json").read_text())

win_ids, win_vecs = [], []
for rid, w in recs.items():
    for start in np.arange(0, max(len(w) / SR - 5, 1), 25):
        win_ids.append(rid)
        win_vecs.append(audio(w[int(start * SR): int((start + 30) * SR)]))
win_vecs = np.stack(win_vecs)
ids = list(tr)
doc_vecs = texts([f"title: none | text: {tr[i]}" for i in ids])

fold = lambda s: s.replace("ł", "l").replace("Ł", "L").lower()  # noqa: E731
db = sqlite3.connect(":memory:")
db.execute("create virtual table f using fts5(t, tokenize='unicode61 remove_diacritics 2')")
db.executemany("insert into f(rowid, t) values (?, ?)", ((n, fold(tr[i])) for n, i in enumerate(ids)))


def bm25(q, prefix):
    toks = [t for t in re.findall(r"\w+", fold(q)) if len(t) > 2]
    expr = " OR ".join((f'"{t[:max(4, len(t) - 2)]}"*' if prefix else f'"{t}"') for t in toks)
    return [ids[r] for (r,) in db.execute("select rowid from f where f match ? order by bm25(f)", (expr,))]


def rank_of(target, order):
    return order.index(target) + 1 if target in order else None


QUERIES = [("krowa", "kw_krowa"), ("krowy na drodze", "kw_krowa"), ("kradzież roweru", "kw_rower"),
           ("rower", "kw_rower"), ("parasol", "kw_parasol"), ("zapomniany parasol i deszcz", "kw_parasol")]
print(f"{'query':30s} audio30s  asr_vec  bm25_exact  bm25_prefix")
for q, target in QUERIES:
    qv = texts([f"task: search result | query: {q}"])[0]
    s = win_vecs @ qv
    best = {}
    for i, rid in enumerate(win_ids):
        best[rid] = max(best.get(rid, -1), s[i])
    a_order = sorted(best, key=lambda r: -best[r])
    v_order = [ids[i] for i in np.argsort(-(doc_vecs @ qv))]
    print(f"{q:30s} {str(rank_of(target, a_order)):>8s} {str(rank_of(target, v_order)):>8s} "
          f"{str(rank_of(target, bm25(q, False))):>11s} {str(rank_of(target, bm25(q, True))):>12s}")

"""Phase 0 spike: audio-embedding search vs transcript search (synthetic corpus).

Methods (recording-level ranking, score = best chunk/window of a recording):
  audio_full    one audio embedding per recording (cap raised to 320 s)
  audio_30s     audio windows 30 s / hop 25 s
  audio_trunc   one audio embedding with the default 30 s processor cap
  asr_vec       Whisper transcript chunks (~30 s) -> text embedding
  asr_bm25      Whisper transcript chunks -> FTS5 BM25 (Polish l-stroke folded)
  hybrid        RRF(asr_vec, asr_bm25), k=60
  hybrid_audio  RRF(audio_30s, asr_vec, asr_bm25), k=60
  oracle_vec    original script text -> text embedding (upper bound, no ASR errors)

Single process, one model (570M text+audio, MPS bf16).
"""

import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from quality_data import LONG_SEGMENTS, QUERIES, RECORDINGS  # noqa: E402

SR = 16000
CORPUS = Path(__file__).parent / "data" / "corpus"
manifest = json.loads((CORPUS / "manifest.json").read_text())
transcripts = json.loads((CORPUS / "transcripts.json").read_text())

model = SentenceTransformer(
    "google/embeddinggemma-2", revision="914f7f89142e33e77833254d9c9b90c3cef7303b", device="mps",
    config_kwargs={"vision_config": None}, model_kwargs={"torch_dtype": torch.bfloat16},
)
tf = model[0]


def enc_audio(wave):
    return model.encode({"audio": {"array": wave, "sampling_rate": SR}}, normalize_embeddings=True)


def enc_docs(texts):
    return model.encode(texts, prompt_name="Document", normalize_embeddings=True, batch_size=8)


def fold(s: str) -> str:
    return s.replace("ł", "l").replace("Ł", "L")


# --- chunks: (rec_id, start_s, end_s, payload) ---
audio_full, audio_trunc, audio_win = [], [], []
for rid in manifest:
    w, sr = sf.read(CORPUS / f"{rid}.wav", dtype="float32")
    assert sr == SR
    tf.processing_kwargs = {"audio": {"max_length": 320 * SR, "truncation": True}}
    audio_full.append((rid, 0, len(w) / SR, enc_audio(w)))
    for start in np.arange(0, max(len(w) / SR - 5, 1), 25):
        seg = w[int(start * SR): int((start + 30) * SR)]
        audio_win.append((rid, float(start), float(start) + len(seg) / SR, enc_audio(seg)))
    tf.processing_kwargs = {}
    audio_trunc.append((rid, 0, 30.0, enc_audio(w)))
    torch.mps.empty_cache()

asr_chunks = []  # group Whisper segments into ~30 s chunks
for rid, t in transcripts.items():
    cur, cs = [], None
    for s in t["segments"]:
        cs = s["start"] if cs is None else cs
        cur.append(s["text"])
        if s["end"] - cs >= 30:
            asr_chunks.append((rid, cs, s["end"], " ".join(cur).strip()))
            cur, cs = [], None
    if cur:
        asr_chunks.append((rid, cs, t["segments"][-1]["end"], " ".join(cur).strip()))
asr_vecs = enc_docs([c[3] for c in asr_chunks])

oracle = [(rid, text) for rid, (_, text) in RECORDINGS.items()] + [("pl_dlugie", t) for _, t in LONG_SEGMENTS]
oracle_vecs = enc_docs([t for _, t in oracle])

db = sqlite3.connect(":memory:")
db.execute("create virtual table fts using fts5(text, tokenize='unicode61 remove_diacritics 2')")
db.executemany("insert into fts(rowid, text) values (?, ?)", ((i, fold(c[3])) for i, c in enumerate(asr_chunks)))


def rank_vec(qv, items, vecs):
    """Return [(rec_id, best_chunk_idx)] ordered by best chunk similarity."""
    sims = vecs @ qv
    best = {}
    for i, (rid, *_rest) in enumerate(items):
        if rid not in best or sims[i] > sims[best[rid]]:
            best[rid] = i
    return sorted(best.items(), key=lambda kv: -sims[kv[1]])


def rank_bm25(q):
    toks = [t for t in re.findall(r"\w+", fold(q).lower()) if len(t) > 1]
    if not toks:
        return []
    expr = " OR ".join(f'"{t}"' for t in toks)
    rows = db.execute("select rowid from fts where fts match ? order by bm25(fts) limit 50", (expr,)).fetchall()
    out, seen = [], set()
    for (i,) in rows:
        rid = asr_chunks[i][0]
        if rid not in seen:
            seen.add(rid)
            out.append((rid, i))
    return out


def rrf(*rankings, k=60):
    score = defaultdict(float)
    for r in rankings:
        for pos, (rid, _) in enumerate(r):
            score[rid] += 1 / (k + pos + 1)
    return [(rid, None) for rid, _ in sorted(score.items(), key=lambda kv: -kv[1])]


def stack(items):
    return np.stack([it[3] for it in items])


A_FULL, A_WIN, A_TR = stack(audio_full), stack(audio_win), stack(audio_trunc)
qvecs = model.encode([q for q, _, _ in QUERIES], prompt_name="SearchQuery", normalize_embeddings=True)
segs = {s["id"]: s for s in manifest["pl_dlugie"]["segments"]}

methods = ["audio_full", "audio_trunc", "audio_30s", "asr_vec", "asr_bm25", "hybrid", "hybrid_audio", "oracle_vec"]
rr = defaultdict(list)
seg_hits = defaultdict(list)
per_query = []
for qi, (q, target, seg) in enumerate(QUERIES):
    qv = qvecs[qi]
    r_win = rank_vec(qv, audio_win, A_WIN)
    r_asr = rank_vec(qv, asr_chunks, asr_vecs)
    r_bm = rank_bm25(q)
    ranks = {
        "audio_full": rank_vec(qv, audio_full, A_FULL),
        "audio_trunc": rank_vec(qv, audio_trunc, A_TR),
        "audio_30s": r_win,
        "asr_vec": r_asr,
        "asr_bm25": r_bm,
        "hybrid": rrf(r_asr, r_bm),
        "hybrid_audio": rrf(r_win, r_asr, r_bm),
        "oracle_vec": rank_vec(qv, [(rid, 0, 0) for rid, _ in oracle], oracle_vecs),
    }
    row = {"q": q, "target": target}
    for m in methods:
        ids = [rid for rid, _ in ranks[m]]
        pos = ids.index(target) + 1 if target in ids else None
        rr[m].append(1 / pos if pos else 0.0)
        row[m] = pos
    if seg:  # does the best window of the long recording land in the right segment?
        s = segs[seg]
        for m, items in [("audio_30s", audio_win), ("asr_vec", asr_chunks)]:
            idx = dict(ranks[m]).get("pl_dlugie")
            _, st, en, *_ = items[idx]
            mid = (st + en) / 2
            seg_hits[m].append(s["start_s"] <= mid <= s["end_s"])
    per_query.append(row)

print(f"{'method':14s} hit@1  MRR")
summary = {}
for m in methods:
    h1 = np.mean([x == 1.0 for x in rr[m]])
    mrr = np.mean(rr[m])
    summary[m] = {"hit@1": round(float(h1), 3), "mrr": round(float(mrr), 3)}
    print(f"{m:14s} {h1:.2f}  {mrr:.3f}")
print("long-recording segment localisation:", {m: f"{sum(v)}/{len(v)}" for m, v in seg_hits.items()})
print("\nper query rank (1 = best):")
print("  " + " | ".join(m[:10] for m in methods) + " | query")
for row in per_query:
    print("  " + " | ".join(f"{str(row[m]):>10s}" for m in methods) + f" | {row['q']}")
(CORPUS / "quality_results.json").write_text(json.dumps({"summary": summary, "per_query": per_query,
    "segments": {m: v for m, v in seg_hits.items()}}, indent=1, ensure_ascii=False))

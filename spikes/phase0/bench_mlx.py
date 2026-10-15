"""Phase 0 spike: MLX port (mlx-community/embeddinggemma-2-*) via mlx-vlm branch.

Run with spikes/phase0/.venv-mlx. Uses the HF transformers processor (same
preprocessing as sentence-transformers), MLX for the forward pass.

Usage: python bench_mlx.py <repo> [--text-only]
"""

import json
import os
import resource
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import mlx.core as mx  # noqa: E402
import numpy as np  # noqa: E402
import psutil  # noqa: E402
import soundfile as sf  # noqa: E402
from huggingface_hub import snapshot_download  # noqa: E402
from mlx_vlm.embedding_loader import load_embedding_model  # noqa: E402
from transformers import AutoProcessor  # noqa: E402

SR = 16000
DATA = Path(__file__).parent / "data"
repo = sys.argv[1]
text_only = "--text-only" in sys.argv

path = Path(snapshot_download(repo))
os.environ["HF_HUB_OFFLINE"] = "1"
proc = AutoProcessor.from_pretrained("google/embeddinggemma-2", revision="914f7f89142e33e77833254d9c9b90c3cef7303b")

out = {"repo": repo, "text_only": text_only, "rss_before_mb": round(psutil.Process().memory_info().rss / 2**20)}
t0 = time.perf_counter()
model = load_embedding_model(path)
if text_only:  # drop the towers we do not need
    model.audio_tower = model.embed_audio = None
    model.vision_tower = model.embed_vision = None
    import gc
    gc.collect()
mx.eval(model.parameters())
out["load_s"] = round(time.perf_counter() - t0, 2)
mx.reset_peak_memory()
out["metal_after_load_mb"] = round(mx.get_active_memory() / 2**20)
out["rss_after_load_mb"] = round(psutil.Process().memory_info().rss / 2**20)


def run(inputs):
    e = model(**{k: mx.array(v) for k, v in inputs.items()}).text_embeds
    mx.eval(e)
    return np.array(e.astype(mx.float32))


def embed_texts(texts):
    return run(proc.tokenizer(texts, padding=True, return_tensors="np"))


def embed_audio(w, max_s=30):
    inp = proc(text="<|audio|>", audio=[w], sampling_rate=SR, return_tensors="np",
               audio_kwargs={"max_length": max_s * SR, "truncation": True})
    return run(inp)[0]


ref = np.load(DATA / "st_reference.npz")
tv = embed_texts(list(ref["texts"]))
out["text_cos_vs_st_fp32"] = np.round((tv * ref["text_vecs"]).sum(1), 4).tolist()

chunk = ("Lantern indeksuje pliki lokalnie i pozwala je przeszukiwać po znaczeniu. " * 40)[:2400]
batch = ["title: none | text: " + chunk + str(i) for i in range(64)]
embed_texts(batch[:16])
t0 = time.perf_counter()
for i in range(0, 64, 16):
    embed_texts(batch[i:i + 16])
out["text_chunks_per_s"] = round(64 / (time.perf_counter() - t0), 1)

if not text_only:
    cos = []
    for rid, rv in zip(ref["audio_ids"], ref["audio_vecs"]):
        w, _ = sf.read(DATA / "corpus" / f"{rid}.wav", dtype="float32")
        cos.append(float(embed_audio(w[: 30 * SR]) @ rv))
    out["audio_cos_vs_st_fp32"] = {"min": round(min(cos), 4), "mean": round(float(np.mean(cos)), 4)}
    w = (0.3 * np.sin(2 * np.pi * 440 * np.arange(300 * SR) / SR)).astype(np.float32)
    for s in [30, 300]:
        embed_audio(w[: s * SR], s)
        t0 = time.perf_counter()
        embed_audio(w[: s * SR], s)
        out[f"audio_{s}s_time_s"] = round(time.perf_counter() - t0, 2)

out["metal_peak_mb"] = round(mx.get_peak_memory() / 2**20)
out["rss_peak_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20)
out["rss_end_mb"] = round(psutil.Process().memory_info().rss / 2**20)
print(json.dumps(out, ensure_ascii=False))

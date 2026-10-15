"""Phase 0 spike: does the ST/processor pipeline truncate long audio?

Builds clips whose content changes after N seconds and checks whether the
embedding changes, plus how many audio tokens the processor emits.
"""

import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import numpy as np  # noqa: E402
import torch  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

SR = 16000
MODEL_ID = "google/embeddinggemma-2"
REVISION = "914f7f89142e33e77833254d9c9b90c3cef7303b"

model = SentenceTransformer(
    MODEL_ID, revision=REVISION, device="mps",
    config_kwargs={"vision_config": None}, model_kwargs={"torch_dtype": torch.bfloat16},
)
proc = model[0].processor
rng = np.random.default_rng(0)


def tone(sec, f):
    t = np.arange(int(sec * SR)) / SR
    return (0.3 * np.sin(2 * np.pi * f * t)).astype(np.float32)


def noise(sec):
    return (0.3 * rng.standard_normal(int(sec * SR))).astype(np.float32)


def emb(wave):
    return model.encode({"audio": {"array": wave, "sampling_rate": SR}}, normalize_embeddings=True)


for sec in [10, 11, 30, 60, 300, 400]:
    w = tone(sec, 440)
    feats = proc(text="<|audio|>", audio=[w], sampling_rate=SR, return_tensors="pt")
    shapes = {k: tuple(v.shape) for k, v in feats.items() if hasattr(v, "shape")}
    print(f"{sec:>4}s processor ->", shapes)

for split in [5, 10, 15, 30, 60, 200]:
    head = tone(split, 440)
    a = emb(np.concatenate([head, tone(30, 440)]))
    b = emb(np.concatenate([head, noise(30)]))
    print(f"change after {split:>3}s: cos(same-head, diff-tail) = {float(a @ b):.4f}")

# --- Raise the feature-extractor cap to the model's documented ~327 s limit. ---
MAX_S = 320
model[0].processing_kwargs = {"audio": {"max_length": MAX_S * SR, "truncation": True}}
print(f"\nwith processing_kwargs audio.max_length={MAX_S}s")
for split in [30, 60, 200, 300]:
    head = tone(split, 440)
    a = emb(np.concatenate([head, tone(30, 440)]))
    b = emb(np.concatenate([head, noise(30)]))
    print(f"change after {split:>3}s: cos(same-head, diff-tail) = {float(a @ b):.4f}")
import time  # noqa: E402
for sec in [60, 300]:
    w = tone(sec, 440)
    emb(w)
    torch.mps.synchronize(); t0 = time.perf_counter(); e = emb(w); torch.mps.synchronize()
    print(f"{sec}s full: {time.perf_counter()-t0:.2f}s, nan={bool(np.isnan(e).any())}, mps={torch.mps.driver_allocated_memory()/2**20:.0f}MB")

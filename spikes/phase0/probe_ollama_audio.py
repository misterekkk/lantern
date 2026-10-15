"""Phase 0 spike: Ollama audio vectors vs sentence-transformers; length cap."""

import base64
import io
import os
import sys
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import numpy as np  # noqa: E402
import requests  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

SR = 16000
URL = "http://127.0.0.1:11434/api/embed"
MODEL = sys.argv[1] if len(sys.argv) > 1 else "embeddinggemma-2:570m-bf16-text-audio"
rng = np.random.default_rng(0)


def wav_b64(w):
    buf = io.BytesIO()
    sf.write(buf, w, SR, format="WAV", subtype="PCM_16")
    return base64.b64encode(buf.getvalue()).decode()


def ollama(w):
    r = requests.post(URL, json={"model": MODEL, "input": [{"audio": wav_b64(w)}]}, timeout=600)
    r.raise_for_status()
    v = np.array(r.json()["embeddings"][0], dtype=np.float32)
    return v / np.linalg.norm(v)


def tone(sec, f=440):
    t = np.arange(int(sec * SR)) / SR
    return (0.3 * np.sin(2 * np.pi * f * t)).astype(np.float32)


def noise(sec):
    return (0.3 * rng.standard_normal(int(sec * SR))).astype(np.float32)


st = SentenceTransformer(
    "google/embeddinggemma-2", revision="914f7f89142e33e77833254d9c9b90c3cef7303b", device="mps",
    config_kwargs={"vision_config": None}, model_kwargs={"torch_dtype": torch.float32},
)
st[0].processing_kwargs = {"audio": {"max_length": 320 * SR, "truncation": True}}

speech, sr = sf.read("spikes/phase0/data/probe_pl.wav", dtype="float32")
assert sr == SR
clips = {"speech_pl": speech, "tone10": tone(10), "speech+noise40": np.concatenate([speech, noise(40)])}
for name, w in clips.items():
    a = st.encode({"audio": {"array": w, "sampling_rate": SR}}, normalize_embeddings=True)
    b = ollama(w)
    print(f"{name:16s} cos(ollama, st) = {float(a @ b):.4f}")

print(f"\n{MODEL}: tail sensitivity")
for split in [30, 60, 200, 300]:
    head = tone(split)
    a = ollama(np.concatenate([head, tone(30)]))
    b = ollama(np.concatenate([head, noise(30)]))
    print(f"change after {split:>3}s: cos = {float(a @ b):.4f}")
for sec in [60, 300, 400]:
    w = tone(sec)
    try:
        t0 = time.perf_counter(); ollama(w); print(f"{sec}s: {time.perf_counter() - t0:.2f}s ok")
    except Exception as exc:  # noqa: BLE001
        print(f"{sec}s: error {exc}")

"""Phase 0 spike: dump sentence-transformers reference vectors for the MLX comparison."""

import json
import os
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

SR = 16000
DATA = Path(__file__).parent / "data"
CORPUS = DATA / "corpus"
TEXTS = [
    "task: search result | query: optymalizacja drzewa decyzyjnego",
    "title: README.md | text: Projekt implementuje przycinanie drzewa decyzyjnego (pruning) i dobór głębokości.",
    "task: search result | query: krowa",
    "title: none | text: def backoff_wrapper(fn, retries=3): ...",
]

m = SentenceTransformer("google/embeddinggemma-2", revision="914f7f89142e33e77833254d9c9b90c3cef7303b", device="mps",
                        config_kwargs={"vision_config": None}, model_kwargs={"torch_dtype": torch.float32})
m[0].processing_kwargs = {"audio": {"max_length": 30 * SR, "truncation": True}}
manifest = json.loads((CORPUS / "manifest.json").read_text())
audio_ids, audio_vecs = [], []
for rid in manifest:
    w, _ = sf.read(CORPUS / f"{rid}.wav", dtype="float32")
    audio_ids.append(rid)
    audio_vecs.append(m.encode({"audio": {"array": w[: 30 * SR], "sampling_rate": SR}}, normalize_embeddings=True))
np.savez(DATA / "st_reference.npz", texts=np.array(TEXTS), text_vecs=m.encode(TEXTS, normalize_embeddings=True),
         audio_ids=np.array(audio_ids), audio_vecs=np.stack(audio_vecs))
print("saved", len(TEXTS), "texts,", len(audio_ids), "audio clips")

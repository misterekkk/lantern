"""Phase 0 spike: Ollama /api/embed for EmbeddingGemma 2 (text + audio).

Compares Ollama text vectors with sentence-transformers and probes audio
request shapes. Requires `ollama serve` on 127.0.0.1:11434.
"""

import base64
import json
import os
import sys
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import numpy as np  # noqa: E402
import requests  # noqa: E402

URL = "http://127.0.0.1:11434/api/embed"
TEXTS = [
    "task: search result | query: jak zrobić pierogi",
    "title: none | text: Przepis na pierogi ruskie: ziemniaki, twaróg, cebula.",
    "title: none | text: ConnectionRefusedError: [Errno 61] Connection refused",
]


def embed(model, inp, **extra):
    r = requests.post(URL, json={"model": model, "input": inp, **extra}, timeout=300)
    return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text


def main(mode):
    if mode == "text":
        import torch
        from sentence_transformers import SentenceTransformer

        st = SentenceTransformer(
            "google/embeddinggemma-2", revision="914f7f89142e33e77833254d9c9b90c3cef7303b",
            device="mps", config_kwargs={"vision_config": None, "audio_config": None},
            model_kwargs={"torch_dtype": torch.float32},
        )
        ref = st.encode(TEXTS, normalize_embeddings=True)
        for tag in ["270m-bf16-text", "570m-bf16-text-audio", "570m-nvfp4-text-audio"]:
            m = f"embeddinggemma-2:{tag}"
            embed(m, TEXTS[:1])
            t0 = time.perf_counter()
            code, body = embed(m, TEXTS)
            dt = time.perf_counter() - t0
            if code != 200:
                print(tag, code, body)
                continue
            v = np.array(body["embeddings"], dtype=np.float32)
            v /= np.linalg.norm(v, axis=1, keepdims=True)
            cos = (v * ref).sum(1)
            print(f"{tag}: dim={v.shape[1]} cos_vs_st={np.round(cos, 4).tolist()} {dt:.2f}s")
    else:
        wav = sys.argv[2]
        b64 = base64.b64encode(open(wav, "rb").read()).decode()
        m = "embeddinggemma-2:570m-bf16-text-audio"
        shapes = {
            "input[{audio:b64}]": {"input": [{"audio": b64}]},
            "input[{type:audio,data}]": {"input": [{"type": "audio", "data": b64}]},
            "input[{type:audio,audio}]": {"input": [{"type": "audio", "audio": b64}]},
            "input[{audios:[b64]}]": {"input": [{"audios": [b64]}]},
            "input+audios": {"input": [""], "audios": [b64]},
            "input+images(audio)": {"input": ["<|audio|>"], "images": [b64]},
        }
        for name, payload in shapes.items():
            r = requests.post(URL, json={"model": m, **payload}, timeout=300)
            txt = r.text
            if r.ok:
                e = r.json().get("embeddings")
                txt = f"OK dim={len(e[0]) if e else None} n={len(e or [])}"
            print(f"{name:28s} {r.status_code} {txt[:200]}")


if __name__ == "__main__":
    main(sys.argv[1])

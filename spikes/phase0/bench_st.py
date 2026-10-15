"""Phase 0 spike: sentence-transformers backend for EmbeddingGemma 2.

Measures load time, memory and throughput for text-only (270M) and
text+audio (570M) configurations on a given device/dtype.

Usage: python bench_st.py --config text|audio --device mps|cpu --dtype bf16|fp32
"""

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("DO_NOT_TRACK", "1")

import numpy as np  # noqa: E402
import psutil  # noqa: E402
import torch  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

MODEL_ID = "google/embeddinggemma-2"
REVISION = "914f7f89142e33e77833254d9c9b90c3cef7303b"
DATA = Path(__file__).parent / "data"

DOCS = [
    "def parse_config(path):\n    with open(path) as f:\n        return yaml.safe_load(f)",
    "Spotkanie zespołu w sprawie budżetu na przyszły kwartał przełożono na czwartek.",
    "The quarterly revenue grew by twelve percent thanks to new enterprise customers.",
    "Przepis na pierogi ruskie: ziemniaki, twaróg, cebula, sól i pieprz.",
    "ConnectionRefusedError: [Errno 61] Connection refused while connecting to Redis",
]
QUERIES = [
    ("funkcja wczytująca plik yaml", 0),
    ("kiedy jest narada o pieniądzach", 1),
    ("how did sales do last quarter", 2),
    ("jak zrobić pierogi", 3),
    ("redis cannot connect error", 4),
]


def rss_mb() -> float:
    return psutil.Process().memory_info().rss / 2**20


def mps_mb() -> float:
    if torch.backends.mps.is_available():
        return torch.mps.driver_allocated_memory() / 2**20
    return 0.0


def sync(device: str) -> None:
    if device == "mps":
        torch.mps.synchronize()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", choices=["text", "audio"], default="text")
    ap.add_argument("--device", choices=["mps", "cpu"], default="mps")
    ap.add_argument("--dtype", choices=["bf16", "fp32"], default="bf16")
    args = ap.parse_args()

    config_kwargs = {"vision_config": None}
    if args.config == "text":
        config_kwargs["audio_config"] = None
    dtype = torch.bfloat16 if args.dtype == "bf16" else torch.float32

    out: dict = {"config": args.config, "device": args.device, "dtype": args.dtype}
    out["rss_before_mb"] = round(rss_mb())
    t0 = time.perf_counter()
    model = SentenceTransformer(
        MODEL_ID,
        revision=REVISION,
        device=args.device,
        config_kwargs=config_kwargs,
        model_kwargs={"torch_dtype": dtype},
    )
    out["load_s"] = round(time.perf_counter() - t0, 2)
    out["params_m"] = round(sum(p.numel() for p in model.parameters()) / 1e6)
    out["rss_after_load_mb"] = round(rss_mb())
    out["mps_after_load_mb"] = round(mps_mb())

    # Sanity: retrieval on tiny set.
    d = model.encode(DOCS, prompt_name="Document", normalize_embeddings=True)
    q = model.encode([x for x, _ in QUERIES], prompt_name="SearchQuery", normalize_embeddings=True)
    sims = q @ d.T
    out["dim"] = int(d.shape[1])
    out["nan"] = bool(np.isnan(d).any() or np.isnan(q).any())
    out["sanity_top1"] = f"{sum(int(sims[i].argmax() == j) for i, (_, j) in enumerate(QUERIES))}/{len(QUERIES)}"

    # Text throughput: ~512-token chunks.
    chunk = ("Lantern indeksuje pliki lokalnie i pozwala je przeszukiwać po znaczeniu. " * 40)[:2400]
    batch = [chunk + str(i) for i in range(64)]
    model.encode(batch[:8], prompt_name="Document")
    sync(args.device)
    t0 = time.perf_counter()
    model.encode(batch, prompt_name="Document", batch_size=16)
    sync(args.device)
    dt = time.perf_counter() - t0
    out["text_tokens_per_chunk"] = len(model.tokenizer(chunk)["input_ids"])
    out["text_chunks_per_s"] = round(len(batch) / dt, 1)

    if args.config == "audio":
        res = {}
        for name in ["bench_60s.wav", "bench_300s.wav"]:
            p = DATA / name
            if not p.exists():
                continue
            model.encode({"audio": str(p)})
            sync(args.device)
            t0 = time.perf_counter()
            e = model.encode({"audio": str(p)})
            sync(args.device)
            dt = time.perf_counter() - t0
            secs = float(name.split("_")[1].rstrip("s.wav"))
            res[name] = {"s": round(dt, 2), "audio_s_per_s": round(secs / dt, 1), "nan": bool(np.isnan(e).any())}
        out["audio"] = res
        # Over-limit behaviour (> ~327 s).
        p = DATA / "bench_400s.wav"
        if p.exists():
            try:
                e = model.encode({"audio": str(p)})
                out["audio_400s"] = f"ok (no error; nan={bool(np.isnan(e).any())}) - check truncation"
            except Exception as exc:  # noqa: BLE001
                out["audio_400s"] = f"error: {type(exc).__name__}: {str(exc)[:200]}"

    out["rss_peak_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20)
    out["mps_peak_mb"] = round(mps_mb())
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())

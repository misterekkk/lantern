"""Phase 17: what the MLX memory limit costs and saves, per kind of indexing work (DECISIONS.md, "MLX limits").

One fresh process per measurement, run one after another (never two models at once): it sets the limits, loads the
model, processes a fixed sample and prints peak physical footprint (Activity Monitor's "Memory"), MLX's own peak and
the time. Usage:

    uv run python spikes/phase17/memory_limits.py all --photos ~/Desktop/Portugalia --media <video>
    uv run python spikes/phase17/memory_limits.py run text 1024      # one measurement (limit in MB, or "default")
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                   "DO_NOT_TRACK": "1"})  # fmt: skip

WORKLOADS = ("text", "image", "siglip", "audio", "whisper")
LIMITS = ("default", "3072", "2048", "1536", "1024", "768", "512", "dynamic")
REPO = Path(__file__).resolve().parents[2]


def sample_texts() -> list[str]:
    """~200 passages of ~1 200 characters from the repository's docs: real prose and code, the chunk size used."""
    from lantern.embedding.base import format_document

    text = "\n".join(p.read_text() for p in sorted((REPO / "docs").glob("*.md")))
    parts = [text[i : i + 1200] for i in range(0, min(len(text), 240_000), 1200)]
    return [format_document(f"docs/{i}.md", p) for i, p in enumerate(parts[:200])]


def run(workload: str, limit: str, photos: Path | None, media: Path | None) -> dict:
    import mlx.core as mx

    from lantern.embedding import mlx_backend
    from lantern.service.perf import footprint

    # the backend sets the limit at load, as the worker does; "default" = MLX's own (1.5x the recommended working set)
    default = int(mx.device_info()["max_recommended_working_set_size"] * 1.5)
    mlx_backend.MEMORY_LIMIT = default if limit == "default" else None if limit == "dynamic" else int(limit) * 2**20
    t0 = time.perf_counter()
    items = 0
    if workload == "text":
        texts = sample_texts()
        mlx_backend.MLXEmbedder().embed_text(texts)
        items = len(texts)
    elif workload in ("image", "siglip"):
        from lantern.extractors.image import load_image

        paths = sorted(p for p in photos.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".heic", ".png"))[:40]
        images = [load_image(p) for p in paths]
        if workload == "image":
            mlx_backend.MLXEmbedder().embed_images(images)
        else:
            from lantern.embedding import siglip

            mlx_backend.MLXEmbedder()._load_model(with_audio=False)  # the worker's limits are set by this load
            siglip.SiglipImageEmbedder().embed_images(images)
        items = len(images)
    elif workload == "audio":
        from lantern.extractors import media as media_mod

        samples = media_mod.decode_all(media, max_seconds=180)
        rate = mlx_backend.AUDIO_SAMPLE_RATE
        hop, win = 25 * rate, mlx_backend.AUDIO_WINDOW_S * rate
        windows = [samples[i : i + win] for i in range(0, max(1, len(samples) - win + 1), hop)]
        mlx_backend.MLXEmbedder().embed_audio(windows)
        items = len(windows)
    elif workload == "whisper":
        from lantern.extractors import media as media_mod
        from lantern.transcription.whisper import WhisperTranscriber

        samples = media_mod.decode_all(media, max_seconds=180)
        mlx_backend.MLXEmbedder()._load_model(with_audio=False)  # in the worker the text model sets the limits first
        WhisperTranscriber().transcribe(samples)
        items = round(len(samples) / 16000)
    seconds = time.perf_counter() - t0
    _, peak = footprint(os.getpid())
    from lantern.service import machine

    mem = machine.read()
    return {"workload": workload, "limit_mb": limit, "applied_mb": round(mlx_backend._limit / 2**20),
            "free_percent": mem.free_percent, "ram_gb": round(mem.total / machine.GB),
            "items": items, "seconds": round(seconds, 1),
            "peak_footprint_mb": round(peak), "mlx_peak_mb": round(mx.get_peak_memory() / 2**20)}  # fmt: skip


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("run", "all"))
    ap.add_argument("workload", nargs="?")
    ap.add_argument("limit", nargs="?", default="default")
    ap.add_argument("--photos", type=Path, default=Path.home() / "Desktop" / "Portugalia")
    ap.add_argument("--media", type=Path)
    ap.add_argument("--workloads", default=",".join(WORKLOADS))
    ap.add_argument("--limits", default=",".join(LIMITS))
    args = ap.parse_args()
    if args.mode == "run":
        print(json.dumps(run(args.workload, args.limit, args.photos, args.media)))
        return 0
    for workload in args.workloads.split(","):
        for limit in args.limits.split(","):
            cmd = [sys.executable, __file__, "run", workload, limit, "--photos", str(args.photos)]
            if args.media:
                cmd += ["--media", str(args.media)]
            out = subprocess.run(cmd, capture_output=True, text=True)
            line = out.stdout.strip().splitlines()[-1] if out.returncode == 0 and out.stdout.strip() else None
            print(line or json.dumps({"workload": workload, "limit_mb": limit, "error": out.stderr.strip()[-400:]}),
                  flush=True)  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())

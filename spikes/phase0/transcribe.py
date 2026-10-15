"""Phase 0 spike: transcribe the corpus with mlx-whisper (for transcript-based search)."""

import json
import os
import resource
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import mlx_whisper  # noqa: E402

CORPUS = Path(__file__).parent / "data" / "corpus"
MODEL = "mlx-community/whisper-large-v3-turbo"

out, total_audio, t0 = {}, 0.0, time.perf_counter()
manifest = json.loads((CORPUS / "manifest.json").read_text())
for rid, meta in manifest.items():
    r = mlx_whisper.transcribe(str(CORPUS / f"{rid}.wav"), path_or_hf_repo=MODEL, language=meta["lang"])
    out[rid] = {"text": r["text"].strip(), "segments": [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in r["segments"]]}
    total_audio += meta["duration_s"]
dt = time.perf_counter() - t0
(CORPUS / "transcripts.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
print(f"{total_audio:.0f}s audio in {dt:.1f}s (incl. model load) -> {total_audio / dt:.1f}x realtime, "
      f"peak RSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20:.0f} MB")
print(out["pl_awaria"]["text"][:600])

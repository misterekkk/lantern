"""Phase 14 spike: does decoding with AVFoundation instead of ffmpeg change what the models see?

For each file: EmbeddingGemma 2 audio vectors of the same windows (cosine, ffmpeg vs AVFoundation) and Whisper's
transcript of both (word-level similarity). Runs the real models (one process, sequentially). Usage:
uv run python spikes/phase14/compare_models.py <avdecode binary> <file>...
"""

import difflib
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from compare_audio import align, avf, ffmpeg  # noqa: E402

from lantern.config import AUDIO_HOP_S, AUDIO_SAMPLE_RATE, AUDIO_WINDOW_S  # noqa: E402

SR = AUDIO_SAMPLE_RATE


def windows(samples: np.ndarray) -> list[np.ndarray]:
    w, h = int(AUDIO_WINDOW_S * SR), int(AUDIO_HOP_S * SR)
    return [samples[i : i + w] for i in range(0, max(1, len(samples) - w + h), h)]


def main() -> None:
    binary, files = sys.argv[1], sys.argv[2:]
    pairs = []
    for f in files:
        a, b = ffmpeg(f), avf(binary, f)
        if b is None:
            continue
        s = align(a, b)
        b = b[-s:] if s < 0 else np.concatenate([np.zeros(s, np.float32), b])  # same start, as Lantern would index
        pairs.append((Path(f).name, a, b))

    from lantern.service.worker import make_embedder

    emb = make_embedder()
    for name, a, b in pairs:
        va, vb = emb.embed_audio(windows(a)), emb.embed_audio(windows(b))
        n = min(len(va), len(vb))
        cos = np.sum(va[:n] * vb[:n], axis=1)
        print(f"EG2 audio  {name[-34:]:34} windows {n:3}  cos min {cos.min():.4f}  mean {cos.mean():.4f}")
    emb.unload_audio()
    del emb

    from lantern.transcription.whisper import WhisperTranscriber

    whisper = WhisperTranscriber()
    for name, a, b in pairs:
        ta = " ".join(s.text for s in whisper.transcribe(a)).split()
        tb = " ".join(s.text for s in whisper.transcribe(b)).split()
        ratio = difflib.SequenceMatcher(None, ta, tb).ratio()
        diff = [d for d in difflib.ndiff(ta, tb) if d[:1] in "+-"][:12]
        print(f"Whisper    {name[-34:]:34} words {len(ta):4} / {len(tb):4}  same words {ratio:.3f}  {' '.join(diff)}")


if __name__ == "__main__":
    main()

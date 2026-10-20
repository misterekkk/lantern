"""Whisper large-v3-turbo on MLX (mlx-whisper). Offline: the model must be in the Hugging Face cache (README).

Loaded for a batch of transcription jobs and released afterwards (~1.8 GB), never together with the
EmbeddingGemma audio tower.
"""

import gc
import math
import re
from collections import Counter
from typing import Protocol

import numpy as np

from lantern.chunking.transcript import Segment
from lantern.config import enforce_offline

REPO = "mlx-community/whisper-large-v3-turbo"
REVISION = "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb"


# Whisper invents text on sound without speech (rain, animals, music): "! ! !", "f f f f", "deep deep deep", a lone
# "Thank you." Measured on such files vs speech (docs/DECISIONS.md): repetitive output has a compression ratio of 5–10
# (speech ~1.4; 2.4 is Whisper's own limit), guessed output an average log-probability of -1.9 or NaN (speech -0.2 to
# -0.4). mlx-whisper's no_speech_prob was always 0, so it is not used.
MAX_COMPRESSION = 2.4
MIN_LOGPROB = -1.0
MIN_WORDS_PER_S = 0.1  # less than this over the whole recording: no speech ("Thank you." in a 25 s clip)


def _noise_text(text: str) -> bool:
    words = re.findall(r"[^\W\d_]+", text.lower())
    if not words:
        return True  # punctuation only ("! ! !")
    top = Counter(words).most_common(1)[0][1]
    return len(words) >= 4 and top / len(words) >= 0.5  # one word over and over


def keep_speech(segments: list[dict], duration_s: float) -> list[dict]:
    """Whisper's segments without invented text; [] when what is left is too little to be speech."""
    kept = []
    for s in segments:
        logprob = s.get("avg_logprob", 0.0)
        if s.get("compression_ratio", 0.0) > MAX_COMPRESSION or logprob is None or math.isnan(logprob):
            continue
        if logprob < MIN_LOGPROB or _noise_text(s.get("text", "")):
            continue
        kept.append(s)
    words = sum(len(re.findall(r"[^\W\d_]+", s["text"])) for s in kept)
    return kept if duration_s <= 0 or words / duration_s >= MIN_WORDS_PER_S else []


class Transcriber(Protocol):
    def transcribe(self, samples: np.ndarray) -> list[Segment]: ...

    def unload(self) -> None: ...


class ModelNotDownloaded(RuntimeError):
    pass


def available() -> bool:
    """True when the Whisper weights are in the local Hugging Face cache."""
    from lantern import models

    return models.is_downloaded(REPO, REVISION)


class WhisperTranscriber:
    def __init__(self, repo: str = REPO, revision: str = REVISION):
        self.repo, self.revision = repo, revision
        self._path: str | None = None

    def _model_path(self) -> str:
        if self._path is None:
            enforce_offline()
            from huggingface_hub import snapshot_download
            from huggingface_hub.errors import LocalEntryNotFoundError

            try:
                self._path = snapshot_download(self.repo, revision=self.revision, local_files_only=True)
            except LocalEntryNotFoundError as exc:
                raise ModelNotDownloaded(
                    f"Whisper model {self.repo}@{self.revision[:8]} is not downloaded; see README (hf download ...)"
                ) from exc
        return self._path

    def transcribe(self, samples: np.ndarray) -> list[Segment]:
        """The raw soundtrack, without an initial prompt: tested on a real recording, a prompt from the file name
        blended names into its words ("Average" -> "Outreach"), and loudness normalisation or denoising added
        invented words; file names often have nothing to do with the recording anyway (docs/DECISIONS.md)."""
        import mlx_whisper

        from lantern.embedding.mlx_backend import apply_memory_limit

        apply_memory_limit(force=True)  # Whisper may run before the text model in a worker: limits first
        result = mlx_whisper.transcribe(
            samples,
            path_or_hf_repo=self._model_path(),
            language=None,  # auto-detect (Polish, English, ...)
            condition_on_previous_text=False,  # fewer repetition loops on long recordings
            verbose=None,
        )
        kept = keep_speech(result["segments"], len(samples) / 16000)
        return [Segment(float(s["start"]), float(s["end"]), s["text"]) for s in kept]

    def unload(self) -> None:
        try:
            import mlx.core as mx
            from mlx_whisper.transcribe import ModelHolder
        except ImportError:
            return
        ModelHolder.model = None
        ModelHolder.model_path = None
        gc.collect()
        mx.clear_cache()


class FakeTranscriber:
    """Tests: returns fixed text per call, split into 10 s segments."""

    def __init__(self, text: str = "fake transcript"):
        self.text = text
        self.calls = 0
        self.unloaded = 0

    def transcribe(self, samples: np.ndarray) -> list[Segment]:
        self.calls += 1
        duration = len(samples) / 16000
        n = max(1, int(duration // 10))
        return [Segment(i * 10.0, min((i + 1) * 10.0, duration), self.text) for i in range(n)]

    def unload(self) -> None:
        self.unloaded += 1

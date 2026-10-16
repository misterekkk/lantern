"""Whisper large-v3-turbo on MLX (mlx-whisper). Offline: the model must be in the Hugging Face cache (README).

Loaded for a batch of transcription jobs and released afterwards (~1.8 GB), never together with the
EmbeddingGemma audio tower.
"""

import gc
import re
from pathlib import Path
from typing import Protocol

import numpy as np

from lantern.chunking.transcript import Segment
from lantern.config import enforce_offline

REPO = "mlx-community/whisper-large-v3-turbo"
REVISION = "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb"


class Transcriber(Protocol):
    def transcribe(self, samples: np.ndarray, hint: str | None = None) -> list[Segment]: ...

    def unload(self) -> None: ...


class ModelNotDownloaded(RuntimeError):
    pass


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

    def transcribe(self, samples: np.ndarray, hint: str | None = None) -> list[Segment]:
        """hint: words the recording probably contains (names from its file name), as Whisper's initial prompt:
        it biases spelling ("Smith" instead of "Smyth") without changing the language detection."""
        import mlx_whisper

        result = mlx_whisper.transcribe(
            samples,
            path_or_hf_repo=self._model_path(),
            language=None,  # auto-detect (Polish, English, ...)
            initial_prompt=hint or None,
            condition_on_previous_text=False,  # fewer repetition loops on long recordings
            verbose=None,
        )
        return [Segment(float(s["start"]), float(s["end"]), s["text"]) for s in result["segments"]]

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
        self.hint: str | None = None

    def transcribe(self, samples: np.ndarray, hint: str | None = None) -> list[Segment]:
        self.calls += 1
        self.hint = hint
        duration = len(samples) / 16000
        n = max(1, int(duration // 10))
        return [Segment(i * 10.0, min((i + 1) * 10.0, duration), self.text) for i in range(n)]

    def unload(self) -> None:
        self.unloaded += 1


def vocabulary_hint(path: Path) -> str:
    """Names a recording probably mentions, from its file and folder names: "Smith_Jones_Brown.mp4" in
    "team-project-2" -> "Smith, Jones, Brown, team, project". Generic camera names (IMG_1234) give nothing."""
    words = re.findall(r"[^\W\d_]{3,}", f"{path.stem} {path.parent.name}")
    generic = {
        "img",
        "mov",
        "vid",
        "video",
        "audio",
        "recording",
        "nagranie",
        "screen",
        "untitled",
        "new",
        "copy",
        "dcim",
    }
    return ", ".join(dict.fromkeys(w for w in words if w.lower() not in generic))

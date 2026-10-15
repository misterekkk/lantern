"""EmbeddingGemma 2 on MLX (ADR-001): mlx-community 8-bit weights via the pinned mlx-vlm branch.

The text tower stays loaded (~275 MB). The audio tower (~0.9 GB) is loaded only for audio jobs and released with
unload_audio(); the vision tower is never loaded. Text is tokenised with `tokenizers` (tokenizer.json); audio goes
through the model's own processor (mel features, capped explicitly: the default silently truncates at 30 s).
Loading is strictly offline: the weights must already be in the Hugging Face cache (see README).
"""

import gc
from pathlib import Path

import numpy as np

from lantern.config import AUDIO_SAMPLE_RATE, AUDIO_WINDOW_S, enforce_offline
from lantern.store.db import ModelInfo

REPO = "mlx-community/embeddinggemma-2-8bit"
REVISION = "7505ef2f8ddef45efef6d060865f27989b3c9cec"
DIM = 768
MAX_TOKENS = 1024  # chunks are ~400 tokens (<= 1600 chars); hard cap protects memory
BATCH = 8  # ~1.1 GB MLX peak on M4 for ~500-token chunks
# MLX allocator limits (16 GB machine shared with the user's apps).
MEMORY_LIMIT = 3 * 1024**3
CACHE_LIMIT = 256 * 1024**2


class ModelNotDownloaded(RuntimeError):
    pass


class MLXEmbedder:
    def __init__(self, repo: str = REPO, revision: str = REVISION, batch_size: int = BATCH):
        self.repo, self.revision, self.batch_size = repo, revision, batch_size
        self._path: Path | None = None
        self._model = None
        self._tokenizer = None
        self._processor = None
        self._has_audio = False

    def model_info(self) -> ModelInfo:
        return ModelInfo(name=self.repo, revision=self.revision, backend="mlx", dtype="8bit", dim=DIM)

    def _snapshot(self) -> Path:
        if self._path is None:
            enforce_offline()
            from huggingface_hub import snapshot_download
            from huggingface_hub.errors import LocalEntryNotFoundError

            try:
                self._path = Path(snapshot_download(self.repo, revision=self.revision, local_files_only=True))
            except LocalEntryNotFoundError as exc:
                raise ModelNotDownloaded(
                    f"model {self.repo}@{self.revision[:8]} is not downloaded; see README (hf download ...)"
                ) from exc
        return self._path

    def _load_model(self, with_audio: bool) -> None:
        import mlx.core as mx

        from lantern.embedding.mlx_loader import load_embedding_gemma2

        mx.set_memory_limit(MEMORY_LIMIT)
        mx.set_cache_limit(CACHE_LIMIT)
        self._model = None
        gc.collect()
        model = load_embedding_gemma2(self._snapshot(), lazy=True)
        if not with_audio:  # drop towers before their weights are materialised
            model.audio_tower = model.embed_audio = None
        model.vision_tower = model.embed_vision = None
        mx.eval(model.parameters())
        self._model, self._has_audio = model, with_audio

    def load(self) -> None:
        if self._model is not None:
            return
        from tokenizers import Tokenizer

        self._load_model(with_audio=False)
        tok = Tokenizer.from_file(str(self._snapshot() / "tokenizer.json"))
        tok.enable_truncation(MAX_TOKENS)
        tok.enable_padding(direction="right", pad_id=tok.token_to_id("<pad>"), pad_token="<pad>")
        self._tokenizer = tok

    def embed_text(self, texts: list[str]) -> np.ndarray:
        import mlx.core as mx

        self.load()
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))  # less padding per batch
        for start in range(0, len(order), self.batch_size):
            idx = order[start : start + self.batch_size]
            enc = self._tokenizer.encode_batch([texts[i] for i in idx])
            ids = mx.array([e.ids for e in enc])
            mask = mx.array([e.attention_mask for e in enc])
            emb = self._model(input_ids=ids, attention_mask=mask).text_embeds
            mx.eval(emb)
            out[idx] = np.array(emb.astype(mx.float32))
        mx.clear_cache()
        return out

    def _ensure_audio(self) -> None:
        self.load()
        if not self._has_audio:
            self._load_model(with_audio=True)
        if self._processor is None:
            from transformers import AutoProcessor

            self._processor = AutoProcessor.from_pretrained(self._snapshot())

    def embed_audio(self, windows: list[np.ndarray]) -> np.ndarray:
        import mlx.core as mx

        self._ensure_audio()
        out = np.zeros((len(windows), DIM), dtype=np.float32)
        cap = AUDIO_WINDOW_S * AUDIO_SAMPLE_RATE
        for i, wave in enumerate(windows):
            inputs = self._processor(
                text="<|audio|>",
                audio=[np.asarray(wave, dtype=np.float32)[:cap]],
                sampling_rate=AUDIO_SAMPLE_RATE,
                return_tensors="np",
                audio_kwargs={"max_length": cap, "truncation": True},
            )
            emb = self._model(**{k: mx.array(v) for k, v in inputs.items()}).text_embeds
            mx.eval(emb)
            out[i] = np.array(emb.astype(mx.float32))[0]
        mx.clear_cache()
        return out

    def unload_audio(self) -> None:
        if self._model is not None and self._has_audio:
            import mlx.core as mx

            self._model.audio_tower = self._model.embed_audio = None
            self._has_audio = False
            gc.collect()
            mx.clear_cache()

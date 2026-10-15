"""EmbeddingGemma 2 on MLX (ADR-001): mlx-community 8-bit weights via the pinned mlx-vlm branch.

Text tower only in this phase; the audio and vision towers are dropped before their weights are materialised.
Tokenisation uses `tokenizers` directly with the model's tokenizer.json. Loading is strictly offline: the weights
must already be in the Hugging Face cache (see README).
"""

import numpy as np

from lantern.config import enforce_offline
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
        self._model = None
        self._tokenizer = None

    def model_info(self) -> ModelInfo:
        return ModelInfo(name=self.repo, revision=self.revision, backend="mlx", dtype="8bit", dim=DIM)

    def load(self) -> None:
        if self._model is not None:
            return
        enforce_offline()
        from pathlib import Path

        import mlx.core as mx
        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import LocalEntryNotFoundError
        from mlx_vlm.embedding_loader import load_embedding_model
        from tokenizers import Tokenizer

        try:
            path = Path(snapshot_download(self.repo, revision=self.revision, local_files_only=True))
        except LocalEntryNotFoundError as exc:
            raise ModelNotDownloaded(
                f"model {self.repo}@{self.revision[:8]} is not downloaded; see README (hf download ...)"
            ) from exc

        mx.set_memory_limit(MEMORY_LIMIT)
        mx.set_cache_limit(CACHE_LIMIT)
        model = load_embedding_model(path, lazy=True)
        model.audio_tower = model.embed_audio = None
        model.vision_tower = model.embed_vision = None
        mx.eval(model.parameters())

        tok = Tokenizer.from_file(str(path / "tokenizer.json"))
        tok.enable_truncation(MAX_TOKENS)
        tok.enable_padding(direction="right", pad_id=tok.token_to_id("<pad>"), pad_token="<pad>")
        self._model, self._tokenizer = model, tok

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

"""Deterministic bag-of-words embedder for tests (no model, no network)."""

import hashlib
import re

import numpy as np

from lantern.store.db import ModelInfo

_TOKEN = re.compile(r"\w+", re.UNICODE)
_PREFIX = re.compile(r"^(task: [^|]+\| query: |title: [^|]*\| text: )")


class FakeEmbedder:
    def __init__(self, dim: int = 64):
        self.dim = dim
        self.calls = 0
        self.texts_embedded = 0

    def model_info(self) -> ModelInfo:
        return ModelInfo(name="fake-bow", revision="0", backend="fake", dtype="f32", dim=self.dim)

    def embed_text(self, texts: list[str]) -> np.ndarray:
        self.calls += 1
        self.texts_embedded += len(texts)
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in _TOKEN.findall(_PREFIX.sub("", text).lower()):
                bucket = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=4).digest(), "little")
                out[row, bucket % self.dim] += 1.0
            norm = np.linalg.norm(out[row])
            out[row] = out[row] / norm if norm else 1.0 / np.sqrt(self.dim)
        return out

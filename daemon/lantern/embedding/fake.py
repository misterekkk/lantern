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
        self.audio_windows = 0
        self.audio_unloads = 0
        self.images_embedded = 0
        self.vision_unloads = 0

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

    def embed_audio(self, windows: list[np.ndarray]) -> np.ndarray:
        """Deterministic vector from coarse signal statistics (tests check plumbing, not quality)."""
        self.audio_windows += len(windows)
        out = np.zeros((len(windows), self.dim), dtype=np.float32)
        for row, w in enumerate(windows):
            energy = float(np.sqrt(np.mean(np.square(w)))) if len(w) else 0.0
            out[row, int(energy * 1000) % self.dim] = 1.0
            out[row, len(w) % self.dim] += 0.5
            out[row] /= np.linalg.norm(out[row])
        return out

    def unload_audio(self) -> None:
        self.audio_unloads += 1

    def embed_images(self, images: list) -> np.ndarray:
        """Deterministic vector from the mean colour of each image (tests check plumbing, not quality)."""
        self.images_embedded += len(images)
        out = np.zeros((len(images), self.dim), dtype=np.float32)
        for row, image in enumerate(images):
            r, g, b = (int(c) for c in np.asarray(image.convert("RGB").resize((1, 1)))[0, 0])
            out[row, (r * 3 + g * 5 + b * 7) % self.dim] = 1.0
        return out

    def unload_vision(self) -> None:
        self.vision_unloads += 1


class FakeImageEmbedder:
    """Second photo model for tests (its own space, like SigLIP)."""

    def __init__(self, dim: int = 32):
        self.dim = dim
        self.images_embedded = 0
        self.unloads = 0

    def model_info(self) -> ModelInfo:
        return ModelInfo(name="fake-siglip", revision="0", backend="fake", dtype="f32", dim=self.dim)

    def embed_images(self, images: list) -> np.ndarray:
        self.images_embedded += len(images)
        out = np.zeros((len(images), self.dim), dtype=np.float32)
        for row, image in enumerate(images):
            r, g, b = (int(c) for c in np.asarray(image.convert("RGB").resize((1, 1)))[0, 0])
            out[row, (r + g + b) % self.dim] = 1.0
        return out

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            out[row, sum(map(ord, text)) % self.dim] = 1.0
        return out

    def unload(self) -> None:
        self.unloads += 1

"""Embedder interface and EmbeddingGemma 2 prompt formats."""

from typing import Protocol

import numpy as np

from lantern.store.db import ModelInfo


class Embedder(Protocol):
    def model_info(self) -> ModelInfo: ...

    def embed_text(self, texts: list[str]) -> np.ndarray:
        """Embed already-formatted texts (see format_*). Returns L2-normalised float32 [n, dim]."""
        ...

    def embed_audio(self, windows: list[np.ndarray]) -> np.ndarray:
        """Embed 16 kHz mono float32 windows (no prompt). Same vector space as text. [n, dim]."""
        ...

    def unload_audio(self) -> None:
        """Release audio-only weights when no audio work is pending."""
        ...


class ImageEmbedder(Protocol):
    """Second photo model (SigLIP 2) for the worker: images -> vectors in its own space."""

    def model_info(self) -> ModelInfo: ...

    def embed_images(self, images: list) -> np.ndarray: ...

    def unload(self) -> None: ...


class QueryEmbedder(Protocol):
    """Text side of the second photo model, for queries in the daemon."""

    def model_info(self) -> ModelInfo: ...

    def embed_queries(self, texts: list[str]) -> np.ndarray: ...


IMAGE_CONTEXT = {"pl": "{q} na zdjęciu", "en": "{q}"}


def format_image_query(query: str, lang: str = "en") -> str:
    """Photo search with EmbeddingGemma: short queries get a language cue. Bare one-word queries are read as English
    ("pies" = pies, the food), so a Polish user's "pies" becomes "pies na zdjęciu" (DECISIONS.md, Phase 7)."""
    template = IMAGE_CONTEXT.get(lang[:2], "{q}") if len(query.split()) <= 3 else "{q}"
    return format_query(template.format(q=query))


# Prompt formats from the model's config_sentence_transformers.json / model card.
def format_document(title: str | None, text: str) -> str:
    return f"title: {title or 'none'} | text: {text}"


def format_query(query: str) -> str:
    return f"task: search result | query: {query}"


def document_title(rel_path: str, symbol: str | None) -> str:
    return f"{rel_path}::{symbol}" if symbol else rel_path

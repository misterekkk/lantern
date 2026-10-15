"""Tests with the real EmbeddingGemma 2 (MLX). Run with: uv run pytest -m model (model must be downloaded)."""

import socket
from pathlib import Path

import numpy as np
import pytest
from conftest import write

from lantern.indexer import run_queue
from lantern.scanner.diff import scan_root
from lantern.search.basic import vector_search

pytestmark = pytest.mark.model
REFERENCE = Path(__file__).parent / "data" / "st_reference_text.npz"


@pytest.fixture(scope="module")
def mlx_embedder():
    from lantern.embedding.mlx_backend import MLXEmbedder

    e = MLXEmbedder()
    e.load()
    return e


def test_matches_sentence_transformers_reference(mlx_embedder):
    # Reference: sentence-transformers fp32 with the original google/embeddinggemma-2 weights (Phase 0).
    ref = np.load(REFERENCE)
    vecs = mlx_embedder.embed_text([str(t) for t in ref["texts"]])
    cos = (vecs * ref["vecs"]).sum(axis=1)
    assert vecs.shape == (4, 768)
    assert np.all(cos >= 0.999), cos


def test_no_network_access_while_loading_and_embedding(monkeypatch):
    attempts = []
    real_connect, real_gai = socket.socket.connect, socket.getaddrinfo
    monkeypatch.setattr(socket.socket, "connect", lambda self, a: (attempts.append(a), real_connect(self, a))[1])
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (attempts.append(a[0]), real_gai(*a, **k))[1])
    from lantern.embedding.mlx_backend import MLXEmbedder

    e = MLXEmbedder()  # fresh instance: includes loading
    e.embed_text(["task: search result | query: test"])
    assert attempts == []


def test_semantic_search_finds_paraphrase(conn, tmp_path, mlx_embedder):
    root = tmp_path / "repo"
    write(
        root,
        "ml/README.md",
        "# Projekt\n\nImplementujemy przycinanie (pruning) drzew decyzyjnych, "
        "żeby ograniczyć przeuczenie i dobrać maksymalną głębokość.\n",
    )
    write(root, "web/README.md", "# Sklep\n\nAplikacja do zamawiania pizzy z dostawą do domu.\n")
    write(
        root,
        "net/retry.py",
        "def backoff_wrapper(fn, retries=3):\n    for i in range(retries):\n"
        "        try:\n            return fn()\n        except OSError:\n"
        "            time.sleep(2 ** i)\n",
    )
    scan_root(conn, root)
    run_queue(conn, mlx_embedder)
    assert vector_search(conn, mlx_embedder, "optymalizacja drzewa decyzyjnego")[0].path.endswith("ml/README.md")
    assert vector_search(conn, mlx_embedder, "retry failed network requests")[0].path.endswith("retry.py")


def test_audio_matches_sentence_transformers_reference(mlx_embedder):
    import soundfile as sf

    wave, sr = sf.read(Path(__file__).parent / "data" / "speech_pl.wav", dtype="float32")
    ref = np.load(Path(__file__).parent / "data" / "speech_pl_st_ref.npy")
    vec = mlx_embedder.embed_audio([wave])[0]
    assert sr == 16000 and float(vec @ ref) >= 0.999
    mlx_embedder.unload_audio()
    assert mlx_embedder.embed_text(["task: search result | query: test"]).shape == (1, 768)  # text still works


def test_whisper_transcribes_polish_offline(monkeypatch):
    import soundfile as sf

    from lantern.transcription.whisper import WhisperTranscriber

    attempts = []
    real_connect = socket.socket.connect
    monkeypatch.setattr(socket.socket, "connect", lambda self, a: (attempts.append(a), real_connect(self, a))[1])
    wave, _ = sf.read(Path(__file__).parent / "data" / "speech_pl.wav", dtype="float32")
    t = WhisperTranscriber()
    try:
        text = " ".join(s.text for s in t.transcribe(wave)).lower()
    finally:
        t.unload()
    assert "budżet" in text and "czwartek" in text
    assert attempts == []

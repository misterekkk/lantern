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


def test_daemon_search_with_mlx_from_api_threads(tmp_path, monkeypatch, lantern_home):
    """MLX streams are per-thread. As in production (`lantern daemon run`), the daemon runs on the main thread and
    API requests arrive on server threads: searches must not fail with "There is no Stream(gpu, 0)"."""
    import tempfile
    import threading
    import time

    from lantern.api import protocol
    from lantern.embedding.mlx_backend import MLXEmbedder
    from lantern.service import settings
    from lantern.service.core import Daemon
    from lantern.service.power import PowerState

    sock = Path(tempfile.mkdtemp(prefix="ln", dir="/tmp")) / "d.sock"
    monkeypatch.setenv("LANTERN_SOCKET", str(sock))
    settings.save(settings.Settings(notify=False))
    root = tmp_path / "repo"
    write(root, "README.md", "# Projekt\n\nPrzycinanie drzew decyzyjnych i dobór głębokości.\n")
    d = Daemon(embedder=MLXEmbedder(), watch=False, power_reader=lambda: PowerState())
    outcome = {}

    def client():
        try:
            deadline = time.monotonic() + 60
            while not protocol.running() and time.monotonic() < deadline:
                time.sleep(0.2)
            protocol.call("index", {"path": str(root)})
            outcome["results"] = protocol.call("search", {"query": "drzewa decyzyjne"}, timeout=60)["results"]
        except Exception as exc:  # reported in the main thread
            outcome["error"] = exc
        finally:
            d.stopping.set()

    threading.Thread(target=client, daemon=True).start()
    d.run()  # main thread, like the real daemon
    assert "error" not in outcome, outcome.get("error")
    assert isinstance(outcome["results"], list)


def test_photo_matches_sentence_transformers_reference(mlx_embedder):
    from PIL import Image

    ref = np.load(Path(__file__).parent / "data" / "photo_st_ref.npy")  # ST fp32, 140 image tokens
    vec = mlx_embedder.embed_images([Image.open(Path(__file__).parent / "data" / "photo.png").convert("RGB")])[0]
    mlx_embedder.unload_vision()
    assert float(vec @ ref) >= 0.999


def test_siglip_and_processor_on_mlx_match_torch_and_need_no_torch():
    import subprocess
    import sys
    import tempfile

    code = (
        "import sys, numpy as np\n"
        "for m in ('torch', 'torchvision', 'torchaudio'): sys.modules[m] = None\n"
        "from PIL import Image\n"
        "from lantern.embedding.siglip import SiglipImageEmbedder, SiglipQueryEmbedder\n"
        "from lantern.embedding.processor import load_processor\n"
        "from lantern.embedding.mlx_backend import MLXEmbedder\n"
        "q = SiglipQueryEmbedder().embed_queries(['a house with a red roof', 'pies', 'zupa pomidorowa'])\n"
        "img = Image.open('tests/data/photo.png').convert('RGB')\n"
        "iv = SiglipImageEmbedder().embed_images([img])[0]\n"
        "p = load_processor(MLXEmbedder()._snapshot())\n"
        "_ = p(text=p.image_token, images=[img], return_tensors='np', images_kwargs={'max_soft_tokens': 140})\n"
        "assert sys.modules.get('torch') is None, 'torch imported'\n"
        "np.savez(sys.argv[1], text_vecs=q, image_vec=iv)\n"
    )
    out = Path(tempfile.mkdtemp()) / "siglip.npz"
    subprocess.run([sys.executable, "-c", code, str(out)], check=True)
    got = np.load(out)
    ref = np.load(Path(__file__).parent / "data" / "siglip_torch_ref.npz")
    assert np.all((got["text_vecs"] * ref["text_vecs"]).sum(1) >= 0.999)
    assert float(got["image_vec"] @ ref["image_vec"]) >= 0.9998


def test_text_path_imports_neither_torch_nor_transformers():
    # The daemon embeds queries and text with these two; transformers (and torch behind it) would add hundreds of MB.
    # mlx-vlm releases moved imports around (0.7.12: the model package's processor, the speculative verifier).
    import subprocess
    import sys

    code = (
        "import sys\n"
        "from lantern.embedding.mlx_backend import MLXEmbedder\n"
        "from lantern.embedding.siglip import SiglipQueryEmbedder\n"
        "MLXEmbedder().embed_text(['task: search result | query: hello'])\n"
        "SiglipQueryEmbedder().embed_queries(['a dog'])\n"
        "heavy = [m for m in ('torch', 'transformers') if m in sys.modules]\n"
        "assert not heavy, heavy\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_photo_search_ranks_the_matching_photo_first(conn, tmp_path, mlx_embedder):
    import shutil

    from lantern.embedding.siglip import SiglipImageEmbedder, SiglipQueryEmbedder
    from lantern.search.hybrid import search

    root = tmp_path / "Zdjecia"
    root.mkdir()
    shutil.copy(Path(__file__).parent / "data" / "photo.png", root / "dom.png")
    from PIL import Image, ImageDraw

    soup = Image.new("RGB", (640, 480), (245, 240, 230))
    ImageDraw.Draw(soup).ellipse((170, 110, 470, 410), fill=(200, 40, 30))  # a red bowl from above
    soup.save(root / "talerz.png")
    # Ten plain pictures (flat colours, simple shapes): photos are shown only when their summed z-score reaches
    # PHOTO_SHOW_Z, and with n photos one model's z cannot exceed sqrt(n - 1); two photos alone never could
    # (DECISIONS.md, open question 41). With these the house scores 6.2.
    palette = [
        (30, 90, 200),
        (40, 160, 70),
        (230, 200, 40),
        (120, 60, 160),
        (240, 120, 30),
        (20, 20, 20),
        (200, 200, 210),
        (150, 90, 50),
        (0, 150, 150),
        (220, 80, 140),
        (90, 90, 90),
        (250, 250, 240),
    ]
    for i in range(10):
        plain = Image.new("RGB", (640, 480), palette[i])
        draw, other = ImageDraw.Draw(plain), palette[(i * 5 + 3) % len(palette)]
        shape = (100 + 10 * i, 80, 540 - 10 * i, 400)
        if i % 3 == 0:
            draw.rectangle(shape, fill=other)
        elif i % 3 == 1:
            draw.ellipse(shape, fill=other)
        else:
            draw.line(shape, fill=other, width=20)
        plain.save(root / f"wzor{i:02d}.png")
    scan_root(conn, root)
    run_queue(conn, mlx_embedder, image_embedder=SiglipImageEmbedder())
    resp = search(conn, mlx_embedder, "dom z dachem", log=False, lang="pl", query_embedder=SiglipQueryEmbedder())
    assert resp.photos[0].path.endswith("dom.png") and resp.photos[0].image_rank == 1


def test_no_network_access_with_photo_models(monkeypatch, mlx_embedder):
    from PIL import Image

    from lantern.embedding.siglip import SiglipImageEmbedder, SiglipQueryEmbedder

    attempts = []
    real_connect, real_gai = socket.socket.connect, socket.getaddrinfo
    monkeypatch.setattr(socket.socket, "connect", lambda self, a: (attempts.append(a), real_connect(self, a))[1])
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (attempts.append(a[0]), real_gai(*a, **k))[1])
    photo = Image.open(Path(__file__).parent / "data" / "photo.png").convert("RGB")
    images, queries = SiglipImageEmbedder(), SiglipQueryEmbedder()  # fresh instances: includes loading
    images.embed_images([photo])
    queries.embed_queries(["pies"])
    mlx_embedder.embed_images([photo])
    images.unload(), queries.unload(), mlx_embedder.unload_vision()
    assert attempts == []

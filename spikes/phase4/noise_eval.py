"""Phase 4 spike: search quality on noisy recordings.

The synthetic corpus (Phase 0: 13 recordings + 3 single-keyword clips, PL/EN) is degraded under several conditions,
indexed through the real Lantern pipeline (MLX EmbeddingGemma 2 audio + Whisper large-v3-turbo) and evaluated with
28 queries. Per condition we report, for the target recording:
  hybrid      Lantern's fused ranking (what the user sees)
  audio_vec   vector search over audio-window chunks only
  asr_vec     vector search over transcript chunks only
  keyword     FTS5/BM25 over transcripts
and the word error rate of the transcripts against the original scripts.

Usage: uv run --group spike python spikes/phase4/noise_eval.py [condition ...]
Writes spikes/phase4/data/results.json. One process, models loaded once (~4 GB peak).
"""

import json
import os
import random
import re
import subprocess
import sys
import tempfile
import zlib
from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf
from audiomentations import AddBackgroundNoise, AddColorNoise, AddGaussianSNR, Compose, HighPassFilter, LowPassFilter

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "phase0"))
from quality_data import (  # noqa: E402
    KEYWORD_QUERIES,
    KEYWORD_RECORDINGS,
    LONG_SEGMENTS,
    QUERIES,
    RECORDINGS,
)

from lantern.embedding.mlx_backend import MLXEmbedder  # noqa: E402
from lantern.indexer import run_queue  # noqa: E402
from lantern.scanner.diff import scan_root  # noqa: E402
from lantern.search.basic import keyword_search  # noqa: E402
from lantern.search.hybrid import search  # noqa: E402
from lantern.store import db  # noqa: E402
from lantern.transcription.whisper import WhisperTranscriber  # noqa: E402

SR = 16000
SRC = HERE.parent / "phase0" / "data"
DATA = HERE / "data"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
BABBLE_LINES = [
    ("Daniel", "So the quarterly figures look fine, but we still need to talk about the new office layout and whether "
     "the coffee machine on the third floor will finally be replaced, because everybody keeps complaining about it."),
    ("Zosia", "A wiesz, że w sobotę był ten koncert w parku? Było mnóstwo ludzi, kolejki do jedzenia ciągnęły się "
     "przez pół godziny, ale muzyka była naprawdę dobra i pogoda dopisała, więc nikt nie narzekał."),
    ("Daniel", "Remember to water the plants on the balcony while I'm away, and if the delivery arrives on Tuesday "
     "just leave it in the hallway. The neighbours might drop by with the spare keys in the afternoon."),
]  # fmt: skip


def babble_track() -> Path:
    """Unrelated background conversation (3 voices mixed), so noise never contains query content."""
    out = DATA / "babble.wav"
    if out.exists():
        return out
    DATA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for i, (voice, text) in enumerate(BABBLE_LINES):
            aiff, wav = Path(tmp) / f"b{i}.aiff", Path(tmp) / f"b{i}.wav"
            subprocess.run(["say", "-v", voice, "-r", "175", "-o", str(aiff), text], check=True)
            subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", aiff, "-ar", "16000", "-ac", "1", wav], check=True)
            parts += ["-i", str(wav)]
        mix = "amix=inputs=3:duration=longest:normalize=0,aloop=loop=-1:size=2e9"
        subprocess.run([FFMPEG, "-loglevel", "error", "-y", *parts, "-filter_complex", mix, "-t", "300", str(out)],
                       check=True)  # fmt: skip
    return out


def conditions() -> dict:
    babble = str(babble_track())
    return {
        "clean": None,
        "pink_10db": Compose([AddColorNoise(min_snr_db=10, max_snr_db=10, min_f_decay=-3.01, max_f_decay=-3.01, p=1)]),
        "pink_0db": Compose([AddColorNoise(min_snr_db=0, max_snr_db=0, min_f_decay=-3.01, max_f_decay=-3.01, p=1)]),
        "babble_5db": Compose([AddBackgroundNoise(sounds_path=babble, min_snr_db=5, max_snr_db=5, p=1)]),
        # Phone-like: narrow band + hiss, then 32 kbps MP3 (done with ffmpeg when writing the file).
        "phone": Compose([
            HighPassFilter(min_cutoff_freq=300, max_cutoff_freq=300, p=1),
            LowPassFilter(min_cutoff_freq=3400, max_cutoff_freq=3400, p=1),
            AddGaussianSNR(min_snr_db=15, max_snr_db=15, p=1),
        ]),  # fmt: skip
    }


def sources() -> dict[str, Path]:
    files = {p.stem: p for p in sorted((SRC / "corpus").glob("*.wav"))}
    files |= {p.stem: p for p in sorted((SRC / "keyword").glob("*.wav"))}
    return files


def scripts() -> dict[str, str]:
    out = {rid: text for rid, (_, text) in RECORDINGS.items()} | dict(KEYWORD_RECORDINGS)
    out["pl_dlugie"] = " ".join(text for _, text in LONG_SEGMENTS)
    return out


def build(name: str, transform, folder: Path) -> None:
    seed = zlib.crc32(name.encode())  # reproducible noise per condition
    np.random.seed(seed)
    random.seed(seed)
    folder.mkdir(parents=True, exist_ok=True)
    for rid, src in sources().items():
        wave, sr = sf.read(src, dtype="float32")
        assert sr == SR
        if transform is not None:
            wave = transform(samples=wave, sample_rate=SR)
        wave = np.clip(wave, -1, 1)
        if name == "phone":
            with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
                sf.write(tmp.name, wave, SR)
                subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", tmp.name, "-b:a", "32k",
                                str(folder / f"{rid}.mp3")], check=True)  # fmt: skip
        else:
            sf.write(folder / f"{rid}.wav", wave, SR)


def words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def wer(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    d = np.arange(len(h) + 1)
    for i in range(1, len(r) + 1):
        prev, d[0] = d.copy(), i
        for j in range(1, len(h) + 1):
            d[j] = min(prev[j] + 1, d[j - 1] + 1, prev[j - 1] + (r[i - 1] != h[j - 1]))
    return float(d[len(h)]) / max(1, len(r))


def rank_by_kind(conn, embedder, query: str, kind: str, model_id: int) -> list[str]:
    from lantern.embedding.base import format_query

    qv = embedder.embed_text([format_query(query)])[0].astype(np.float32)
    rows = conn.execute(
        f"SELECT v.chunk_id, v.distance, f.path FROM {db.vec_table(model_id)} v "
        "JOIN chunks c ON c.id = v.chunk_id JOIN files f ON f.id = c.file_id "
        "WHERE v.embedding MATCH ? AND k = 2000 AND c.kind = ? ORDER BY v.distance",
        (qv.tobytes(), kind),
    ).fetchall()
    order = []
    for _, _, path in rows:
        stem = Path(path).stem
        if stem not in order:
            order.append(stem)
    return order


def evaluate(conn, embedder) -> dict:
    model_id = db.find_model(conn, embedder.model_info())
    ranks = defaultdict(list)
    for query, target, _ in QUERIES + KEYWORD_QUERIES:
        lists = {
            "hybrid": [Path(r.path).stem for r in search(conn, embedder, query, limit=50, log=False).results],
            "audio_vec": rank_by_kind(conn, embedder, query, "audio", model_id),
            "asr_vec": rank_by_kind(conn, embedder, query, "transcript", model_id),
            "keyword": [Path(h.path).stem for h in keyword_search(conn, query, 50)],
        }
        for method, order in lists.items():
            ranks[method].append(order.index(target) + 1 if target in order else None)
    summary = {}
    for method, rs in ranks.items():
        summary[method] = {
            "hit@1": round(sum(r == 1 for r in rs) / len(rs), 3),
            "mrr": round(sum(1 / r for r in rs if r) / len(rs), 3),
        }
    texts = scripts()
    transcripts = defaultdict(str)
    for path, text in conn.execute(
        "SELECT f.path, c.text FROM chunks c JOIN files f ON f.id = c.file_id WHERE c.kind = 'transcript' ORDER BY f.path, c.ord"
    ):
        transcripts[Path(path).stem] += " " + text
    wers = [wer(texts[rid], transcripts.get(rid, "")) for rid in texts]
    summary["wer_mean"] = round(float(np.mean(wers)), 3)
    return summary


def main() -> None:
    selected = sys.argv[1:] or list(conditions())
    embedder, transcriber = MLXEmbedder(), WhisperTranscriber()
    results_path = DATA / "results.json"
    results = json.loads(results_path.read_text()) if results_path.exists() else {}
    for name in selected:
        transform = conditions()[name]
        folder, home = DATA / name, DATA / f"home-{name}"
        build(name, transform, folder)
        conn = db.connect(home / "index.db")
        scan_root(conn, folder)
        run_queue(conn, embedder, transcriber=transcriber)
        results[name] = evaluate(conn, embedder)
        conn.close()
        print(name, json.dumps(results[name]), flush=True)
        results_path.write_text(json.dumps(results, indent=1))
    print("\ncondition    " + "  ".join(f"{m:>16s}" for m in ["hybrid", "audio_vec", "asr_vec", "keyword"]) + "   WER")
    for name, r in results.items():
        cells = "  ".join(f"{r[m]['hit@1']:.2f} / {r[m]['mrr']:.3f}".rjust(16) for m in ["hybrid", "audio_vec", "asr_vec", "keyword"])
        print(f"{name:12s} {cells}   {r['wer_mean']:.2f}")


if __name__ == "__main__":
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    main()

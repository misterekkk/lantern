"""Phase 7 study: photo search with EmbeddingGemma 2 (quantisations x image token budgets) and SigLIP 2 (sizes).

Same data and queries as image_eval.py (Flickr30k 1k test, 12 single-word concepts EN/PL, 5000 EN captions,
25 PL captions). Each configuration runs in its own process (run.sh wraps it with /usr/bin/time -l for the peak
memory footprint) and writes data/bench/<tag>.npz + <tag>.json:
  load_s, idle_mb (footprint after loading + warm-up: what a resident worker would hold), img_per_s, query_ms
Quality, per configuration:
  concepts mAP: EN raw ("dog"), PL raw ("pies"), PL with context ("pies na zdjęciu")
  captions R@1: EN (5000), PL (25)

Usage: python image_bench.py eg2 <repo> <budget> | siglip <repo> | report
"""

import ctypes
import json
import os
import re
import struct
import sys
import time
from pathlib import Path

import numpy as np

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ["HF_HUB_OFFLINE"] = "1"

HERE = Path(__file__).parent
OUT = HERE / "data" / "bench"
sys.path.insert(0, str(HERE))
import image_eval as ie  # noqa: E402  (data, concepts, Polish captions)

REVISIONS = {
    "mlx-community/embeddinggemma-2-4bit": "8ab839e127cb8cb305edd71fb7a02c82f2d49ab3",
    "mlx-community/embeddinggemma-2-8bit": "7505ef2f8ddef45efef6d060865f27989b3c9cec",
    "mlx-community/embeddinggemma-2-bf16": "1a4ffddb7905d3f63486748deabe091a01fb6201",
    "google/siglip2-base-patch16-256": "3f9f96cb90da5dbc758b01813f2f6f1aee24c1ab",
    "google/siglip2-large-patch16-256": "787800c8990e6f058423089178e718139608408c",
    "google/siglip2-so400m-patch16-384": "dd658faac399427308559e2c3ac1e99cbe43845d",
}


def footprint_mb() -> float:
    buf = ctypes.create_string_buffer(4096)
    ctypes.CDLL("/usr/lib/libproc.dylib").proc_pid_rusage(os.getpid(), 4, buf)
    return struct.unpack_from("<Q", buf, 72)[0] / 2**20


def queries(data) -> dict[str, list[str]]:
    return {
        "en": [c[0] for c in ie.CONCEPTS],
        "pl": [c[1] for c in ie.CONCEPTS],
        "pl_ctx": [f"{c[1]} na zdjęciu" for c in ie.CONCEPTS],
        "cap_en": [cap for r in data for cap in r["captions"]],
        "cap_pl": list(ie.PL_CAPTIONS.values()),
    }


def save(tag: str, img: np.ndarray, txt: dict[str, np.ndarray], stats: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez(OUT / f"{tag}.npz", img=img, **{f"q_{k}": v for k, v in txt.items()})
    (OUT / f"{tag}.json").write_text(json.dumps(stats, indent=1))
    print(tag, json.dumps(stats), flush=True)


def bench_eg2(repo: str, budget: int) -> None:
    import mlx.core as mx
    from huggingface_hub import snapshot_download
    from transformers import AutoProcessor

    from lantern.embedding.mlx_loader import load_embedding_gemma2

    data = ie.rows()
    t0 = time.perf_counter()
    path = Path(snapshot_download(repo, revision=REVISIONS[repo], local_files_only=True))
    mx.set_memory_limit(4 * 1024**3)
    model = load_embedding_gemma2(path, lazy=True)
    model.audio_tower = model.embed_audio = None
    mx.eval(model.parameters())
    proc = AutoProcessor.from_pretrained(path)
    load_s = time.perf_counter() - t0

    def run(inputs):
        e = model(**{k: mx.array(v) for k, v in inputs.items()}).text_embeds
        mx.eval(e)
        return np.array(e.astype(mx.float32))

    def image(im):
        return run(proc(text=proc.image_token, images=[im], return_tensors="np", images_kwargs={"max_soft_tokens": budget}))

    images = list(ie.load_images([r["file"] for r in data]))
    image(images[0])  # warm-up
    mx.clear_cache()
    idle = footprint_mb()
    t0 = time.perf_counter()
    img = np.concatenate([image(im) for im in images])
    img_s = time.perf_counter() - t0

    def texts(items):
        out = []
        for i in range(0, len(items), 32):
            batch = [f"task: search result | query: {q}" for q in items[i : i + 32]]
            out.append(run(proc.tokenizer(batch, padding=True, return_tensors="np")))
        return np.concatenate(out)

    t0 = time.perf_counter()
    for q in ("pies na zdjęciu", "dog", "zdjęcie plaży"):
        texts([q])
    query_ms = (time.perf_counter() - t0) / 3 * 1000
    txt = {k: texts(v) for k, v in queries(data).items()}
    quant = repo.rsplit("-", 1)[-1]
    save(f"eg2-{quant}-{budget}", img, txt, {
        "model": f"EmbeddingGemma 2 {quant}, {budget} tokenów/zdjęcie", "load_s": round(load_s, 1),
        "idle_mb": round(idle), "img_per_s": round(len(images) / img_s, 2), "query_ms": round(query_ms, 1),
    })  # fmt: skip


def bench_siglip(repo: str, dtype: str = "fp32") -> None:
    import torch
    from transformers import AutoModel, AutoProcessor

    data = ie.rows()
    t0 = time.perf_counter()
    torch_dtype = {"fp32": torch.float32, "fp16": torch.float16}[dtype]
    model = AutoModel.from_pretrained(repo, revision=REVISIONS[repo], torch_dtype=torch_dtype).to("mps").eval()
    proc = AutoProcessor.from_pretrained(repo, revision=REVISIONS[repo])
    load_s = time.perf_counter() - t0

    def feats(out):
        out = out if torch.is_tensor(out) else out.pooler_output
        return torch.nn.functional.normalize(out.float(), dim=-1).cpu().numpy()

    def text(items):
        out = []
        for i in range(0, len(items), 64):
            inp = proc(text=[q.lower() for q in items[i : i + 64]], padding="max_length", max_length=64,
                       truncation=True, return_tensors="pt").to("mps")  # fmt: skip
            out.append(feats(model.get_text_features(**inp)))
        return np.concatenate(out)

    images = list(ie.load_images([r["file"] for r in data]))
    with torch.no_grad():
        def pix(batch):
            return {k: (v.to(torch_dtype) if v.is_floating_point() else v) for k, v in proc(images=batch, return_tensors="pt").to("mps").items()}

        feats(model.get_image_features(**pix(images[:2])))  # warm-up
        torch.mps.synchronize()
        torch.mps.empty_cache()
        idle = footprint_mb()
        t0 = time.perf_counter()
        img = np.concatenate([
            feats(model.get_image_features(**pix(images[i : i + 16])))
            for i in range(0, len(images), 16)
        ])  # fmt: skip
        torch.mps.synchronize()
        img_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        for q in ("pies na zdjęciu", "dog", "zdjęcie plaży"):
            text([q])
        query_ms = (time.perf_counter() - t0) / 3 * 1000
        txt = {k: text(v) for k, v in queries(data).items()}
    name = repo.split("/")[-1].replace("siglip2-", "")
    save(f"siglip2-{name}-{dtype}", img, txt, {
        "model": f"SigLIP 2 {name} {dtype}", "load_s": round(load_s, 1), "idle_mb": round(idle),
        "img_per_s": round(len(images) / img_s, 2), "query_ms": round(query_ms, 1),
    })  # fmt: skip


def report() -> None:
    data = ie.rows()
    rel = {c[0]: np.array([bool(re.search(c[2], " ".join(r["captions"]).lower())) for r in data]) for c in ie.CONCEPTS}
    cap_target = np.array([i for i, r in enumerate(data) for _ in r["captions"]])
    pl_target = np.array([next(i for i, r in enumerate(data) if r["file"] == f) for f in ie.PL_CAPTIONS])
    rows = []
    for stats_file in sorted(p for p in OUT.glob("*.json") if p.name != "report.json"):
        tag = stats_file.stem
        stats = json.loads(stats_file.read_text())
        peak = OUT / f"{tag}.peak"
        stats["peak_mb"] = int(peak.read_text()) if peak.exists() else None
        z = np.load(OUT / f"{tag}.npz")
        img = z["img"]

        def concept_map(key):
            aps = []
            for (en, *_), q in zip(ie.CONCEPTS, z[f"q_{key}"], strict=True):
                hits = rel[en][np.argsort(-(img @ q))]
                prec = np.cumsum(hits) / np.arange(1, len(hits) + 1)
                aps.append((prec * hits).sum() / hits.sum())
            return float(np.mean(aps))

        def r1(key, target):
            return float((np.argmax(z[f"q_{key}"] @ img.T, axis=1) == target).mean())

        dog = rel["dog"][np.argsort(-(img @ z["q_pl"][0]))][:10].sum()
        dog_ctx = rel["dog"][np.argsort(-(img @ z["q_pl_ctx"][0]))][:10].sum()
        rows.append({**stats, "tag": tag, "map_en": concept_map("en"), "map_pl": concept_map("pl"),
                     "map_pl_ctx": concept_map("pl_ctx"), "r1_en": r1("cap_en", cap_target),
                     "r1_pl": r1("cap_pl", pl_target), "pies": f"{dog}/{dog_ctx}"})  # fmt: skip
    head = f"{'konfiguracja':44s} {'zdj/s':>6s} {'spoczynek':>9s} {'szczyt':>7s} {'zapyt.':>6s}  mAP EN  PL  PL+ctx  R@1 EN  PL  pies(goły/ctx)"
    print(head)
    for r in rows:
        peak = f"{r['peak_mb']} MB" if r["peak_mb"] else "-"
        print(f"{r['model']:44s} {r['img_per_s']:6.1f} {r['idle_mb']:6d} MB {peak:>7s} {r['query_ms']:5.0f}ms"
              f"  {r['map_en']:.3f} {r['map_pl']:.3f} {r['map_pl_ctx']:.3f}  {r['r1_en']:.3f} {r['r1_pl']:.2f}  {r['pies']}")  # fmt: skip
    (OUT / "report.json").write_text(json.dumps(rows, indent=1, default=str))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "eg2":
        bench_eg2(sys.argv[2], int(sys.argv[3]))
    elif cmd == "siglip":
        bench_siglip(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "fp32")
    else:
        report()

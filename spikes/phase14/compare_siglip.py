"""Phase 14 spike: SigLIP 2 image tower on MLX (lantern/embedding/siglip_vision.py, fp16) vs the torch fp16 one it
replaces. Vectors of the same photos (cosine), and text→image retrieval on Flickr30k 1k (5000 English captions, the
query side unchanged: the MLX text tower). Usage:
uv run python spikes/phase14/compare_siglip.py [extra photo paths file]
"""

import ast
import csv
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from lantern.embedding import siglip, siglip_vision

FLICKR = Path(__file__).parents[1] / "phase7" / "data" / "flickr" / "images_flickr_1k_test"


def flickr() -> tuple[list[Path], list[list[str]]]:
    csv_path = next(Path.home().glob(".cache/huggingface/hub/datasets--nlphuji--flickr_1k_test_image_text_retrieval/snapshots/*/test_1k_flickr.csv"))
    rows = list(csv.DictReader(csv_path.open()))
    return [FLICKR / r["filename"] for r in rows], [ast.literal_eval(r["raw"]) for r in rows]


def mlx_vectors(model, paths: list[Path]) -> np.ndarray:
    import mlx.core as mx

    out = []
    for i in range(0, len(paths), 16):
        images = [Image.open(p).convert("RGB") for p in paths[i : i + 16]]
        v = model(mx.array(siglip_vision.preprocess(images)).astype(mx.float16)).astype(mx.float32)
        mx.eval(v)
        v = np.array(v)
        out.append(v / np.linalg.norm(v, axis=1, keepdims=True))
    return np.concatenate(out)


def torch_vectors(snap: Path, paths: list[Path]) -> np.ndarray:
    import torch
    from transformers import AutoModel, AutoProcessor

    model = AutoModel.from_pretrained(snap, dtype=torch.float16).to("mps").eval()
    proc = AutoProcessor.from_pretrained(snap)
    out = []
    with torch.no_grad():
        for i in range(0, len(paths), 16):
            images = [Image.open(p).convert("RGB") for p in paths[i : i + 16]]
            inp = {k: (v.half() if v.is_floating_point() else v) for k, v in proc(images=images, return_tensors="pt").to("mps").items()}
            feats = model.get_image_features(**inp)
            feats = feats if torch.is_tensor(feats) else feats.pooler_output
            out.append(torch.nn.functional.normalize(feats.float(), dim=-1).cpu().numpy())
    return np.concatenate(out)


def recall(img: np.ndarray, txt: np.ndarray, owner: np.ndarray) -> tuple[float, float]:
    sims = txt @ img.T
    rank = (sims > sims[np.arange(len(txt)), owner][:, None]).sum(axis=1) + 1
    return float((rank <= 1).mean()), float((rank <= 5).mean())


def main() -> None:
    paths, caps = flickr()
    extra = [Path(p) for p in Path(sys.argv[1]).read_text().split("\n") if p] if len(sys.argv) > 1 else []
    snap = siglip._snapshot(siglip.IMAGE_REPO, siglip.IMAGE_REVISION)
    model = siglip_vision.load(snap)
    b = mlx_vectors(model, paths + extra)
    a = torch_vectors(snap, paths + extra)
    cos = np.sum(a * b, axis=1)
    n = len(paths)
    print(f"cos torch vs MLX  flickr {n}: min {cos[:n].min():.4f} mean {cos[:n].mean():.4f}", end="")
    if extra:
        print(f" | user's photos {len(extra)}: min {cos[n:].min():.4f} mean {cos[n:].mean():.4f}", end="")
    print()
    q = siglip.SiglipQueryEmbedder()
    texts = [c for cs in caps for c in cs]
    owner = np.array([i for i, cs in enumerate(caps) for _ in cs])
    txt = np.concatenate([q.embed_queries(texts[i : i + 64]) for i in range(0, len(texts), 64)])
    txt /= np.linalg.norm(txt, axis=1, keepdims=True)
    for name, img in (("torch fp16", a[:n]), ("MLX fp16", b[:n])):
        r1, r5 = recall(img, txt, owner)
        print(f"{name:10} caption R@1 {r1:.3f}  R@5 {r5:.3f}")


if __name__ == "__main__":
    main()

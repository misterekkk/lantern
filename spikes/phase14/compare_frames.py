"""Phase 14 spike: key frames from lantern-media (AVFoundation) vs ffmpeg (extractors/frames.py), after the same
repeat removal. Compares the kept frames' times, how alike the matched frames look, and (with --vectors) the
EmbeddingGemma 2 image vectors of matched frames. Usage:
uv run python spikes/phase14/compare_frames.py <lantern-media> [--vectors] <video>...
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from lantern.extractors import frames as fr


def avf_frames(binary: str, path: Path) -> list[fr.Frame] | None:
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run([binary, "frames", str(path), tmp, str(fr.SCENE_THRESHOLD), str(fr.MAX_GAP_S),
                            str(fr.FRAME_WIDTH), str(fr.MAX_FRAMES)], capture_output=True, text=True)  # fmt: skip
        if r.returncode != 0:
            return None
        duration, raw = 0.0, []
        for line in r.stdout.splitlines():
            kind, rest = line.split(" ", 1)
            if kind == "duration":
                duration = float(rest)
            else:
                t, f = rest.split(" ", 1)
                with Image.open(f) as im:
                    raw.append((float(t), im.convert("RGB")))
    out, last = [], None
    for t, image in raw:  # the same repeat removal as extract_frames
        sig = fr.signature(image)
        if last is not None and fr.is_repeat(sig, last):
            continue
        if out:
            out[-1].end_s = t
        out.append(fr.Frame(t, duration, image))
        last = sig
    return out


def similarity(a: Image.Image, b: Image.Image) -> float:
    x = np.asarray(a.resize((64, 36)).convert("L"), np.float32)
    y = np.asarray(b.resize((64, 36)).convert("L"), np.float32)
    return float(np.corrcoef(x.ravel(), y.ravel())[0, 1])


def main() -> None:
    binary, args = sys.argv[1], sys.argv[2:]
    vectors = "--vectors" in args
    files = [Path(a) for a in args if a != "--vectors"]
    emb = None
    if vectors:
        from lantern.service.worker import make_embedder

        emb = make_embedder()
    for path in files:
        a = fr.extract_frames(path)
        b = avf_frames(binary, path)
        if b is None:
            print(f"{path.name[-34:]:34} AVFoundation cannot read it")
            continue
        ta, tb = [f.start_s for f in a], [f.start_s for f in b]
        pairs = [(f, min(b, key=lambda g: abs(g.start_s - f.start_s))) for f in a]
        dt = [abs(x.start_s - y.start_s) for x, y in pairs]
        sims = [similarity(x.image, y.image) for x, y in pairs]
        line = (f"{path.name[-34:]:34} ffmpeg {len(a):3} frames  avf {len(b):3}  time diff median {np.median(dt):5.2f}s "
                f"max {max(dt):6.2f}s  look-alike min {min(sims):.3f} mean {np.mean(sims):.3f}  sizes {a[0].image.size}/{b[0].image.size}")  # fmt: skip
        if emb is not None:
            va = emb.embed_images([x.image for x, _ in pairs])
            vb = emb.embed_images([y.image for _, y in pairs])
            cos = np.sum(va * vb, axis=1)
            line += f"  EG2 cos min {cos.min():.3f} mean {cos.mean():.3f}"
        print(line)
        print("   ffmpeg", [round(t, 1) for t in ta][:14])
        print("   avf   ", [round(t, 1) for t in tb][:14])


if __name__ == "__main__":
    main()

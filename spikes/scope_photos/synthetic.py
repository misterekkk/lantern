"""Synthetic folders from Imagenette (10 labelled classes): scoped photo search, current vs library pool vs either.
Builds each layout in its own LANTERN_HOME under <work>, indexes it, measures, prints JSON. Never touches the real
index.   synthetic.py <imagenette val dir> <work dir> <seed> [<seed> ...]"""
import json
import os
import random
import shutil
import sys
from pathlib import Path

val, work, seeds = Path(sys.argv[1]), Path(sys.argv[2]), [int(s) for s in sys.argv[3:]]
os.environ["LANTERN_NOTIFY"] = "0"
sys.path.insert(0, str(Path(__file__).parent))

from bench_data import CLASSES, PER_CLASS  # noqa: E402


pick = random.Random(7)
pool = []  # (source path, class)
for c in CLASSES:
    files = sorted((val / c).glob("*.JPEG"))
    pool += [(f, c) for f in pick.sample(files, PER_CLASS)]


def layout(seed):
    rng = random.Random(seed)
    items = pool[:]
    rng.shuffle(items)
    small = [rng.randint(1, 7) for _ in range(20)]
    rest = len(items) - sum(small)
    large = [rest // 6 + (1 if i < rest % 6 else 0) for i in range(6)]
    folders, i = [], 0
    for k, n in enumerate(small + large):
        name = f"{'maly' if k < 20 else 'duzy'}{k:02d}"
        folders.append((name, items[i : i + n]))
        i += n
    return folders


results = {}
for seed in seeds:
    home = work / f"seed{seed}"
    shutil.rmtree(home, ignore_errors=True)
    root = home / "Zdjecia"
    truth = {}  # path -> class
    folders = layout(seed)
    n_img = 0
    for name, items in folders:
        d = root / name
        d.mkdir(parents=True)
        for src, c in items:
            n_img += 1
            dst = d / f"img_{n_img:04d}.jpg"
            shutil.copy(src, dst)
            truth[str(dst)] = c
    results[seed] = {"home": str(home), "truth": truth, "folders": [(str(root / n), len(it)) for n, it in folders]}
(work / "layouts.json").write_text(json.dumps(results))
print(json.dumps({s: {"images": len(r["truth"]), "small": sum(1 for _, n in r["folders"] if n < 8),
                      "small_images": sum(n for _, n in r["folders"] if n < 8)} for s, r in results.items()}))

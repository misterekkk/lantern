"""Photos: decoded read-only into a downscaled RGB image. Pillow for common formats; macOS `sips` for HEIC/HEIF
(iPhone photos) and anything Pillow cannot read. The source file is never written."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from lantern.extractors.text import SkipFile
from lantern.extractors.vision import flatten

MAX_SIDE = 1024  # both models resize further (EmbeddingGemma ~140 tokens, SigLIP 224 px)
MIN_SIDE = 96  # smaller images are icons and UI assets, not photos
MAX_IMAGE_BYTES = 100 * 1024 * 1024
SIPS_FIRST = frozenset({".heic", ".heif"})


def _via_sips(path: Path) -> Image.Image:
    sips = shutil.which("sips") or "/usr/bin/sips"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "image.jpg"
        res = subprocess.run(
            [sips, "-s", "format", "jpeg", str(path), "--out", str(out)],  # -Z would also upscale small images
            capture_output=True, text=True, timeout=60,
        )  # fmt: skip
        if res.returncode != 0 or not out.exists():
            raise SkipFile(f"cannot decode image: {res.stderr.strip()[:200]}")
        with Image.open(out) as im:
            return im.convert("RGB")


def load_image(path: Path) -> Image.Image:
    size = os.path.getsize(path)
    if size > MAX_IMAGE_BYTES:
        raise SkipFile(f"image larger than {MAX_IMAGE_BYTES // 2**20} MB")
    if path.suffix.lower() in SIPS_FIRST:
        image = _via_sips(path)
    else:
        try:
            with Image.open(path) as im:
                im.seek(0)  # first frame of animated GIF/WebP
                im = ImageOps.exif_transpose(im)
                im.draft("RGB", (MAX_SIDE, MAX_SIDE))  # fast JPEG downscale while decoding
                image = flatten(im)  # transparency onto white (see extractors/vision.py)
        except (OSError, ValueError, Image.DecompressionBombError):
            image = _via_sips(path)
    if min(image.size) < MIN_SIDE:
        raise SkipFile("too small to be a photo (icon or UI asset)")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    return image

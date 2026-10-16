"""Video frames for visual search: ffmpeg scene detection with a minimum sampling rate, then near-duplicate
removal by perceptual hash (dHash) before any embedding. Each kept frame stands for the span until the next one."""

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from lantern.extractors.media import find_ffmpeg
from lantern.extractors.text import SkipFile

SCENE_THRESHOLD = 0.3  # ffmpeg scene score for a cut
MAX_GAP_S = 30.0  # slow changes (lectures, slides) still get a frame at least this often
DHASH_MAX_DISTANCE = 6  # Hamming distance (of 64 bits) under which a frame repeats the previous one
COLOR_MAX_DISTANCE = 24.0  # mean RGB difference (0-255) under which colours count as the same
FRAME_WIDTH = 448
MAX_FRAMES = 400  # per video (about 3 h at one frame per 30 s)

_PTS = re.compile(r"pts_time:([0-9.]+)")


@dataclass
class Frame:
    start_s: float
    end_s: float
    image: Image.Image


def dhash(image: Image.Image) -> int:
    small = np.asarray(image.convert("L").resize((9, 8), Image.BILINEAR), dtype=np.int16)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def signature(image: Image.Image) -> tuple[int, np.ndarray]:
    """dHash (structure) + 4x4 mean colours (dHash alone sees flat colour cards as identical)."""
    colors = np.asarray(image.convert("RGB").resize((4, 4), Image.BILINEAR), dtype=np.float32)
    return dhash(image), colors


def is_repeat(a: tuple[int, np.ndarray], b: tuple[int, np.ndarray]) -> bool:
    return hamming(a[0], b[0]) <= DHASH_MAX_DISTANCE and float(np.abs(a[1] - b[1]).mean()) <= COLOR_MAX_DISTANCE


def extract_frames(path: Path, duration_s: float | None = None) -> list[Frame]:
    select = f"isnan(prev_selected_t)+gt(scene\\,{SCENE_THRESHOLD})+gte(t-prev_selected_t\\,{MAX_GAP_S})"
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "info", "-i", str(path), "-an",
            "-vf", f"select='{select}',scale={FRAME_WIDTH}:-2,showinfo", "-fps_mode", "vfr",
            "-frames:v", str(MAX_FRAMES), "-q:v", "4", f"{tmp}/f%05d.jpg",
        ]  # fmt: skip
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        times = [float(t) for t in _PTS.findall(res.stderr)]  # showinfo: one pts_time per selected frame
        files = sorted(Path(tmp).glob("f*.jpg"))
        if not files:
            if "does not contain any stream" in res.stderr or "matches no streams" in res.stderr or res.returncode == 0:
                raise SkipFile("no video frames")
            raise SkipFile(f"cannot decode video: {res.stderr.strip().splitlines()[-1][:200]}")
        duration = duration_s or _duration(res.stderr) or (times[-1] + MAX_GAP_S if times else 0.0)
        frames: list[Frame] = []
        last_sig: tuple[int, np.ndarray] | None = None
        for i, f in enumerate(files):
            with Image.open(f) as im:
                image = im.convert("RGB")
            start = times[i] if i < len(times) else (frames[-1].start_s + MAX_GAP_S if frames else 0.0)
            sig = signature(image)
            if last_sig is not None and is_repeat(sig, last_sig):
                continue  # same shot as the previous kept frame: it already covers this span
            if frames:
                frames[-1].end_s = start
            frames.append(Frame(start, duration, image))
            last_sig = sig
        return frames


def _duration(stderr: str) -> float | None:
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", stderr)
    if not m:
        return None
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)

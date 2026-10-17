"""Small JPEG thumbnails for clients that cannot read the user's folders (the Raycast extension: macOS permissions
belong to one app, and Raycast cannot show an image passed in memory). Made only on request, only for indexed photos
and videos (a video's frame at the result's moment), into a user-only cache folder, and deleted after
THUMBNAIL_TTL_S. The Lantern app itself never uses this: it draws thumbnails in memory (Quick Look)."""

import hashlib
import os
import subprocess
import time
from pathlib import Path

THUMBNAIL_DIR = Path.home() / "Library" / "Caches" / "Lantern" / "thumbnails"
THUMBNAIL_TTL_S = 600
MAX_SIZE = 640


def prune(folder: Path = THUMBNAIL_DIR, max_age_s: float = THUMBNAIL_TTL_S) -> None:
    """Deletes thumbnails older than max_age_s (all of them with 0)."""
    cutoff = time.time() - max_age_s
    for f in folder.glob("*.jpg") if folder.is_dir() else []:
        try:
            if max_age_s <= 0 or f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def make(path: str, kind: str, start_ms: int | None, size: int = 320, folder: Path = THUMBNAIL_DIR) -> Path | None:
    """A thumbnail of an indexed photo (kind "image") or of a video's frame at start_ms (kind "video"); None for
    anything else or when the file cannot be read."""
    size = max(32, min(int(size), MAX_SIZE))
    src = Path(path)
    try:
        mtime = src.stat().st_mtime_ns
    except OSError:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    prune(folder)
    key = hashlib.sha256(f"{path}\0{mtime}\0{start_ms}\0{size}".encode()).hexdigest()[:32]
    out = folder / f"{key}.jpg"
    if out.exists():
        os.utime(out)  # used again: keep it for another TTL
        return out
    try:
        if kind == "image":
            from lantern.extractors.image import load_image

            image = load_image(src)
            image.thumbnail((size, size))
            image.save(out, "JPEG", quality=80)
        elif kind == "video":
            from lantern.extractors.media import find_ffmpeg

            seconds = f"{max(0, (start_ms or 0)) / 1000:.3f}"
            subprocess.run(
                [find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", seconds, "-i", str(src),
                 "-frames:v", "1", "-vf", f"scale='min({size},iw)':-2", "-q:v", "4", "-y", str(out)],
                capture_output=True, timeout=20, check=True,
            )  # fmt: skip
        else:
            return None
    except Exception:  # an unreadable file gets no thumbnail; the client shows an icon
        out.unlink(missing_ok=True)
        return None
    if not out.exists():
        return None
    os.chmod(out, 0o600)
    return out

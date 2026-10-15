"""Audio and video soundtracks via ffmpeg: 16 kHz mono float32, streamed window by window.

Video files are handled like audio (their soundtrack); frames are out of scope until Phase 7.
ffmpeg only reads the input file; output goes to a pipe.
"""

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from lantern.config import AUDIO_HOP_S, AUDIO_MIN_WINDOW_S, AUDIO_SAMPLE_RATE, AUDIO_WINDOW_S
from lantern.extractors.text import SkipFile

_CANDIDATES = ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg")


class FFmpegMissing(RuntimeError):
    pass


def find_ffmpeg() -> str:
    """LANTERN_FFMPEG, then PATH, then Homebrew locations (brew is often not on a GUI/daemon PATH)."""
    for candidate in (os.environ.get("LANTERN_FFMPEG"), shutil.which("ffmpeg"), *_CANDIDATES):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    raise FFmpegMissing("ffmpeg not found; install it with `brew install ffmpeg` (see README)")


def _decoder(path: Path) -> subprocess.Popen:
    cmd = [
        find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(path), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(AUDIO_SAMPLE_RATE),
        "-f", "f32le", "pipe:1",
    ]  # fmt: skip
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _finish(proc: subprocess.Popen, got_audio: bool) -> None:
    err = proc.stderr.read().decode("utf-8", "replace").strip() if proc.stderr else ""
    code = proc.wait()
    if not got_audio:
        if "matches no streams" in err or "does not contain any stream" in err or not err:
            raise SkipFile("no audio track")
        raise SkipFile(f"cannot decode: {err.splitlines()[-1][:200]}")
    if code != 0:
        raise RuntimeError(f"ffmpeg failed: {err[-300:]}")


def iter_windows(
    path: Path, window_s: float = AUDIO_WINDOW_S, hop_s: float = AUDIO_HOP_S
) -> Iterator[tuple[float, np.ndarray]]:
    """Yield (start_seconds, samples) windows with overlap. Memory stays at ~one window regardless of file length."""
    window, hop = int(window_s * AUDIO_SAMPLE_RATE), int(hop_s * AUDIO_SAMPLE_RATE)
    proc = _decoder(path)
    buf = np.zeros(0, dtype=np.float32)
    start = 0  # sample offset of buf[0]
    got_audio = False
    try:
        while True:
            data = proc.stdout.read(hop * 4)
            if data:
                got_audio = True
                buf = np.concatenate([buf, np.frombuffer(data, dtype=np.float32)])
            while len(buf) >= window:
                yield start / AUDIO_SAMPLE_RATE, buf[:window].copy()
                buf, start = buf[hop:], start + hop
            if not data:
                break
        # Tail: the last partial window, unless it is fully covered by the previous window's overlap.
        covered = start > 0 and len(buf) <= window - hop
        if len(buf) >= AUDIO_MIN_WINDOW_S * AUDIO_SAMPLE_RATE and not covered:
            yield start / AUDIO_SAMPLE_RATE, buf.copy()
    except GeneratorExit:  # consumer stopped early: do not leave ffmpeg running
        proc.kill()
        proc.wait()
        raise
    _finish(proc, got_audio)


def decode_all(path: Path, max_seconds: float | None = None) -> np.ndarray:
    """Whole soundtrack as one array (for Whisper)."""
    proc = _decoder(path)
    limit = int(max_seconds * AUDIO_SAMPLE_RATE) * 4 if max_seconds else -1
    data = proc.stdout.read(limit) if limit > 0 else proc.stdout.read()
    if limit > 0 and len(data) >= limit and proc.poll() is None:
        proc.kill()  # stop decoding beyond the limit; the non-zero exit is expected
        proc.wait()
    else:
        _finish(proc, bool(data))
    return np.frombuffer(data, dtype=np.float32).copy()

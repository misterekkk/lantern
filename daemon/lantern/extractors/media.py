"""Audio and video soundtracks: 16 kHz mono float32, streamed window by window.

Decoded by lantern-media (app/Sources/LanternMedia: AVFoundation, part of macOS, shipped inside Lantern.app), with
ffmpeg as the fallback for what AVFoundation cannot read (MKV, WebM) when it is installed. Measured equal for search
(docs/DECISIONS.md, "Phase 14"). Both only read the input file; output goes to a pipe.
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


_REPO = Path(__file__).resolve().parents[3]
_HELPERS = (
    Path("/Applications/Lantern.app/Contents/MacOS/lantern-media"),
    _REPO / "app" / "build" / "Lantern.app" / "Contents" / "MacOS" / "lantern-media",
    _REPO / "app" / ".build" / "arm64-apple-macosx" / "release" / "lantern-media",
)


def find_media_helper() -> str | None:
    """lantern-media: LANTERN_MEDIA (set by the app to its own copy; "off" forces ffmpeg), else an installed or
    development build."""
    env = os.environ.get("LANTERN_MEDIA")
    if env == "off":
        return None
    for candidate in (env, *_HELPERS):
        if candidate and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def find_ffmpeg() -> str:
    """LANTERN_FFMPEG, then PATH, then Homebrew locations (brew is often not on a GUI/daemon PATH)."""
    for candidate in (os.environ.get("LANTERN_FFMPEG"), shutil.which("ffmpeg"), *_CANDIDATES):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    raise FFmpegMissing("ffmpeg not found; install it with `brew install ffmpeg` (see README)")


def _ffmpeg_decoder(path: Path) -> subprocess.Popen:
    cmd = [
        find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", str(path), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(AUDIO_SAMPLE_RATE),
        "-f", "f32le", "pipe:1",
    ]  # fmt: skip
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _decoder(path: Path, first: int = 4096) -> tuple[subprocess.Popen, bytes]:
    """A running decoder and the first bytes it produced: lantern-media, or ffmpeg when lantern-media is missing or
    cannot read the file (exit 2 before any audio)."""
    helper = find_media_helper()
    if helper:
        proc = subprocess.Popen([helper, "audio", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        head = proc.stdout.read(first)
        if head:
            return proc, head
        err = proc.stderr.read().decode("utf-8", "replace").strip() if proc.stderr else ""
        if proc.wait() != 2:
            raise SkipFile(f"cannot decode: {err[:200]}")
        try:
            find_ffmpeg()
        except FFmpegMissing:
            raise SkipFile(f"{path.suffix or 'this'} files need ffmpeg (brew install ffmpeg): {err[:120]}") from None
    proc = _ffmpeg_decoder(path)
    return proc, b""


def _finish(proc: subprocess.Popen, got_audio: bool) -> None:
    err = proc.stderr.read().decode("utf-8", "replace").strip() if proc.stderr else ""
    code = proc.wait()
    if not got_audio:
        if "matches no streams" in err or "does not contain any stream" in err or not err:
            raise SkipFile("no audio track")
        raise SkipFile(f"cannot decode: {err.splitlines()[-1][:200]}")
    if code != 0:
        raise RuntimeError(f"decoding failed: {err[-300:]}")


def iter_windows(
    path: Path, window_s: float = AUDIO_WINDOW_S, hop_s: float = AUDIO_HOP_S
) -> Iterator[tuple[float, np.ndarray]]:
    """Yield (start_seconds, samples) windows with overlap. Memory stays at ~one window regardless of file length."""
    window, hop = int(window_s * AUDIO_SAMPLE_RATE), int(hop_s * AUDIO_SAMPLE_RATE)
    proc, head = _decoder(path)
    buf = np.zeros(0, dtype=np.float32)
    start = 0  # sample offset of buf[0]
    got_audio = False
    try:
        while True:
            data = head + proc.stdout.read(hop * 4)
            head = b""
            if len(data) % 4:  # keep whole float32 samples (the first read may end mid-sample)
                data += proc.stdout.read(4 - len(data) % 4)
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
    proc, head = _decoder(path)
    limit = int(max_seconds * AUDIO_SAMPLE_RATE) * 4 if max_seconds else -1
    data = head + (proc.stdout.read(max(0, limit - len(head))) if limit > 0 else proc.stdout.read())
    data = data[: len(data) - len(data) % 4]
    if limit > 0 and len(data) >= limit and proc.poll() is None:
        proc.kill()  # stop decoding beyond the limit; the non-zero exit is expected
        proc.wait()
    else:
        _finish(proc, bool(data))
    return np.frombuffer(data, dtype=np.float32).copy()

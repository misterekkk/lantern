"""Phase 14 spike: ffmpeg vs AVFoundation (avdecode.swift) audio decoding, as Lantern uses it (16 kHz mono float32).

Signal level only: duration, the offset between the two (decoders add different priming delays), correlation and
SNR after aligning. Usage: uv run python spikes/phase14/compare_audio.py <avdecode binary> <file>...
"""

import subprocess
import sys
from pathlib import Path

import numpy as np

from lantern.extractors.media import AUDIO_SAMPLE_RATE, find_ffmpeg

SR = AUDIO_SAMPLE_RATE


def ffmpeg(path: str) -> np.ndarray:
    cmd = [find_ffmpeg(), "-nostdin", "-loglevel", "error", "-i", path, "-map", "0:a:0", "-vn", "-ac", "1",
           "-ar", str(SR), "-f", "f32le", "pipe:1"]  # fmt: skip
    return np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, dtype=np.float32)


def avf(binary: str, path: str) -> np.ndarray | None:
    r = subprocess.run([binary, path], capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.float32) if r.returncode == 0 and r.stdout else None


def align(a: np.ndarray, b: np.ndarray, max_shift: int = SR) -> int:
    """Shift of b against a (samples) maximising correlation over the first 20 s."""
    n = min(len(a), len(b), 20 * SR)
    x, y = a[:n], b[:n]
    spec = np.fft.rfft(x, 2 * n) * np.conj(np.fft.rfft(y, 2 * n))
    corr = np.fft.irfft(spec)
    corr = np.concatenate([corr[-max_shift:], corr[: max_shift + 1]])
    return int(np.argmax(corr)) - max_shift


def main() -> None:
    binary, files = sys.argv[1], sys.argv[2:]
    print(f"{'file':38} {'ffmpeg s':>9} {'avf s':>8} {'shift ms':>9} {'corr':>7} {'SNR dB':>7}")
    for f in files:
        name = Path(f).name[-38:]
        a = ffmpeg(f)
        b = avf(binary, f)
        if b is None:
            print(f"{name:38} {len(a) / SR:9.1f} {'cannot read':>8}")
            continue
        s = align(a, b)
        x, y = (a[s:], b) if s >= 0 else (a, b[-s:])
        n = min(len(x), len(y))
        x, y = x[:n], y[:n]
        corr = float(np.corrcoef(x, y)[0, 1]) if n > 1 and x.std() > 0 and y.std() > 0 else float("nan")
        noise = float(np.mean((x - y) ** 2))
        snr = 10 * np.log10(float(np.mean(x**2)) / noise) if noise > 0 else float("inf")
        print(f"{name:38} {len(a) / SR:9.1f} {len(b) / SR:8.1f} {s / SR * 1000:9.1f} {corr:7.4f} {snr:7.1f}")


if __name__ == "__main__":
    main()

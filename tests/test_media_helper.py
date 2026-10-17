"""lantern-media (AVFoundation) against ffmpeg: the same soundtrack, the same cuts, and ffmpeg for what AVFoundation
cannot read. Skipped unless both are available (the helper is built by app/build.sh or `swift build`)."""

import subprocess

import numpy as np
import pytest

from lantern.extractors import frames as fr
from lantern.extractors import media
from lantern.extractors.text import SkipFile

HELPER = media.find_media_helper()
try:
    FFMPEG = media.find_ffmpeg()
except media.FFmpegMissing:
    FFMPEG = None
pytestmark = pytest.mark.skipif(not (HELPER and FFMPEG), reason="needs lantern-media and ffmpeg")


def make(path, *args):
    subprocess.run([FFMPEG, "-nostdin", "-v", "error", *args, "-y", str(path)], check=True)
    return path


def test_soundtrack_matches_ffmpeg(tmp_path, monkeypatch):
    clip = make(tmp_path / "tone.m4a", "-f", "lavfi", "-i", "sine=frequency=440:duration=4:sample_rate=16000",
                "-c:a", "aac")  # fmt: skip
    avf = media.decode_all(clip)
    monkeypatch.setenv("LANTERN_MEDIA", "off")
    ff = media.decode_all(clip)
    n = min(len(avf), len(ff))
    assert abs(len(avf) - len(ff)) < 0.05 * media.AUDIO_SAMPLE_RATE
    assert np.corrcoef(avf[:n], ff[:n])[0, 1] > 0.99


def test_cuts_found_like_ffmpeg(tmp_path, monkeypatch):
    sources = ["-f", "lavfi", "-i", "color=c=red:s=320x240:d=4", "-f", "lavfi", "-i", "testsrc2=s=320x240:d=4"]
    video = make(tmp_path / "cuts.mp4", *sources, "-filter_complex", "[0][1]concat=n=2:v=1", "-pix_fmt", "yuv420p")
    avf = [round(f.start_s) for f in fr.extract_frames(video)]
    monkeypatch.setenv("LANTERN_MEDIA", "off")
    ff = [round(f.start_s) for f in fr.extract_frames(video)]
    assert avf == ff == [0, 4]


def test_mkv_falls_back_to_ffmpeg_or_says_why(tmp_path, monkeypatch):
    clip = make(tmp_path / "tone.mkv", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:a", "libopus")
    assert len(media.decode_all(clip)) > 2 * media.AUDIO_SAMPLE_RATE  # AVFoundation cannot read MKV: ffmpeg did
    monkeypatch.setattr(media, "find_ffmpeg", lambda: (_ for _ in ()).throw(media.FFmpegMissing("x")))
    with pytest.raises(SkipFile, match="need ffmpeg"):
        media.decode_all(clip)

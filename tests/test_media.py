"""Audio and video soundtracks (needs ffmpeg; uses the fake embedder and transcriber)."""

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from lantern.chunking.transcript import Segment, group_segments
from lantern.extractors.media import decode_all, find_ffmpeg, iter_windows
from lantern.extractors.text import SkipFile
from lantern.indexer import run_queue
from lantern.scanner.diff import file_hash, scan_root
from lantern.search.hybrid import search
from lantern.transcription.whisper import FakeTranscriber

try:
    FFMPEG = find_ffmpeg()
except Exception:  # pragma: no cover
    FFMPEG = None
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")
SR = 16000


def tone(path: Path, seconds: float, freq: float = 440.0) -> Path:
    t = np.arange(int(seconds * SR)) / SR
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32), SR)
    return path


def video(path: Path, seconds: int, with_audio: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=blue:s=64x64:d={seconds}"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}", "-shortest"]
    subprocess.run([*cmd, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


@needs_ffmpeg
def test_windows_overlap_and_cover_the_recording(tmp_path):
    windows = list(iter_windows(tone(tmp_path / "a.wav", 70)))
    starts = [s for s, _ in windows]
    assert starts == [0.0, 25.0, 50.0]  # 0-30, 25-55, 50-70
    assert len(windows[0][1]) == 30 * SR and abs(len(windows[-1][1]) / SR - 20) < 0.1


@needs_ffmpeg
def test_short_tail_covered_by_overlap_is_not_repeated(tmp_path):
    starts = [s for s, _ in iter_windows(tone(tmp_path / "a.wav", 53))]
    assert starts == [0.0, 25.0]  # 50-53 lies inside 25-55


@needs_ffmpeg
def test_video_soundtrack_is_decoded_and_silent_video_skipped(tmp_path):
    samples = decode_all(video(tmp_path / "v.mp4", 3))
    assert abs(len(samples) / SR - 3) < 0.1
    with pytest.raises(SkipFile, match="no audio"):
        list(iter_windows(video(tmp_path / "silent.mp4", 2, with_audio=False)))


@needs_ffmpeg
def test_decode_limit(tmp_path):
    samples = decode_all(tone(tmp_path / "long.wav", 20), max_seconds=5)
    assert len(samples) == 5 * SR


def test_transcript_grouping():
    segs = [Segment(i * 7.0, i * 7.0 + 7.0, f"zdanie {i}") for i in range(10)]
    pieces = group_segments(segs, target_s=30)
    assert pieces[0].start_ms == 0 and pieces[0].end_ms == 35000  # 5 segments reach 30 s
    assert pieces[-1].end_ms == 70000 and "zdanie 9" in pieces[-1].text


def test_large_files_are_hashed_by_samples(tmp_path, monkeypatch):
    monkeypatch.setattr("lantern.scanner.diff.PARTIAL_HASH_BYTES", 1024)
    a = tmp_path / "a.bin"
    a.write_bytes(b"x" * 20_000_000)
    h1 = file_hash(a)
    a.write_bytes(b"x" * 10_000_000 + b"y" + b"x" * 9_999_999)  # middle sample changes
    assert file_hash(a) != h1


@needs_ffmpeg
def test_audio_and_video_are_indexed_with_timestamps_and_transcripts(conn, tmp_path, embedder):
    root = tmp_path / "media"
    tone(root / "nagranie.wav", 70)
    video(root / "film.mp4", 40)
    video(root / "bez_dzwieku.mp4", 2, with_audio=False)
    scan_root(conn, root)
    transcriber = FakeTranscriber("krowa stała na środku drogi")
    stats = run_queue(conn, embedder, transcriber=transcriber)

    assert stats.media == 2 and stats.skipped == 1 and stats.transcribed == 2
    assert embedder.audio_windows == 3 + 2 and embedder.audio_unloads == 1 and transcriber.unloaded == 1
    rows = conn.execute(
        "SELECT c.kind, c.start_ms, c.end_ms FROM chunks c JOIN files f ON f.id = c.file_id "
        "WHERE f.path LIKE '%nagranie.wav' ORDER BY c.kind, c.start_ms"
    ).fetchall()
    audio = [(r[1], r[2]) for r in rows if r[0] == "audio"]
    assert audio == [(0, 30000), (25000, 55000), (50000, 70000)]
    assert any(r[0] == "transcript" for r in rows)

    top = search(conn, embedder, "krowa", limit=3).results[0]
    assert top.kind == "transcript" and top.start_ms is not None and "krowa" in top.preview


@needs_ffmpeg
def test_reindex_audio_keeps_transcript_until_replaced(conn, tmp_path, embedder):
    root = tmp_path / "media"
    tone(root / "a.wav", 30)
    scan_root(conn, root)
    run_queue(conn, embedder, transcriber=FakeTranscriber("pierwsza"))
    counts = lambda: dict(conn.execute("SELECT kind, COUNT(*) FROM chunks GROUP BY kind").fetchall())  # noqa: E731
    before = counts()
    assert set(before) == {"audio", "transcript"}
    run_queue(conn, embedder)  # no transcriber: nothing queued, nothing lost
    assert counts() == before


def test_ffmpeg_is_found():
    assert FFMPEG is None or shutil.which(FFMPEG) or Path(FFMPEG).exists()


@needs_ffmpeg
def test_audio_window_match_shows_transcript_of_the_same_time_range(conn, tmp_path, embedder):
    from lantern.search.basic import _hits

    root = tmp_path / "media"
    tone(root / "a.wav", 30)
    scan_root(conn, root)
    run_queue(conn, embedder, transcriber=FakeTranscriber("tekst z nagrania"))
    audio_chunk = conn.execute("SELECT id FROM chunks WHERE kind = 'audio'").fetchone()[0]
    hit = _hits(conn, [(audio_chunk, 0.9)])[0]
    assert hit.kind == "audio" and "tekst z nagrania" in hit.preview

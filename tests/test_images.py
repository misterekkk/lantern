"""Photos and video frames: extraction, two photo models, photo tables, fused photo search (fake models)."""

import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from lantern.embedding.base import format_image_query
from lantern.embedding.fake import FakeEmbedder, FakeImageEmbedder
from lantern.extractors.frames import extract_frames
from lantern.extractors.image import load_image
from lantern.extractors.media import find_ffmpeg
from lantern.extractors.text import SkipFile
from lantern.indexer import run_queue
from lantern.scanner.diff import scan_root
from lantern.search.hybrid import search
from lantern.store import db

try:
    FFMPEG = find_ffmpeg()
except Exception:  # pragma: no cover
    FFMPEG = None
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")
needs_sips = pytest.mark.skipif(shutil.which("sips") is None, reason="macOS sips not available")


def photo(path: Path, color=(200, 30, 30), size=(640, 480)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, color)
    for x in range(0, size[0], 40):  # some structure, not a flat card
        img.paste((255, 255, 255), (x, 0, x + 5, size[1]))
    img.save(path)
    return path


def test_photo_is_loaded_downscaled_and_icons_skipped(tmp_path):
    big = load_image(photo(tmp_path / "a.png", size=(3000, 2000)))
    assert max(big.size) == 1024
    with pytest.raises(SkipFile, match="too small"):
        load_image(photo(tmp_path / "icon.png", size=(64, 64)))
    (tmp_path / "broken.jpg").write_bytes(b"not an image")
    with pytest.raises(SkipFile):
        load_image(tmp_path / "broken.jpg")


@needs_sips
def test_heic_from_iphone_is_decoded(tmp_path):
    png = photo(tmp_path / "src.png")
    heic = tmp_path / "IMG_0001.HEIC"
    subprocess.run(["sips", "-s", "format", "heic", str(png), "--out", str(heic)], check=True, capture_output=True)
    image = load_image(heic)
    assert image.mode == "RGB" and max(image.size) == 640


def test_image_query_gets_language_cue_for_short_queries():
    assert format_image_query("pies", "pl").endswith("query: pies na zdjęciu")
    assert format_image_query("dog", "en").endswith("query: dog")
    long = "dziewczynka w masce na ramionach mężczyzny"
    assert format_image_query(long, "pl").endswith(f"query: {long}")  # long queries carry their own context


def test_photos_are_indexed_by_both_models_and_found(conn, tmp_path):
    root = tmp_path / "Zdjecia"
    photo(root / "czerwone.png", (220, 20, 20))
    photo(root / "niebieskie.jpg", (20, 20, 220))
    eg, sig = FakeEmbedder(), FakeImageEmbedder()
    scan_root(conn, root)
    stats = run_queue(conn, eg, image_embedder=sig)
    assert stats.images == 2 and eg.images_embedded == 2 and sig.images_embedded == 2
    assert eg.vision_unloads == 1 and sig.unloads == 1

    eg_id, sig_id = db.find_model(conn, eg.model_info()), db.find_model(conn, sig.model_info())
    assert conn.execute(f"SELECT COUNT(*) FROM {db.vec_table(eg_id, images=True)}").fetchone()[0] == 2
    assert conn.execute(f"SELECT COUNT(*) FROM {db.vec_table(sig_id, images=True)}").fetchone()[0] == 2
    assert conn.execute(f"SELECT COUNT(*) FROM {db.vec_table(eg_id)}").fetchone()[0] == 0  # not mixed with text

    resp = search(conn, eg, "czerwone", limit=5, log=False, lang="pl", query_embedder=sig)
    # Fake vectors carry no meaning, so the photo is found by its name; it still goes to the photos section.
    assert [r.path.endswith("czerwone.png") for r in resp.photos] == [True] and resp.photos[0].keyword_rank == 1
    assert not [r for r in resp.results if r.kind == "image"]


def test_deleting_a_photo_clears_both_photo_tables(conn, tmp_path):
    root = tmp_path / "Zdjecia"
    p = photo(root / "a.png")
    scan_root(conn, root)
    run_queue(conn, FakeEmbedder(), image_embedder=FakeImageEmbedder())
    p.unlink()
    scan_root(conn, root)
    for table in db.vector_tables(conn):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table


def test_photo_indexing_can_be_turned_off(conn, tmp_path):
    root = tmp_path / "r"
    photo(root / "a.png")
    res = scan_root(conn, root, include_images=False)
    assert not res.new


@needs_ffmpeg
def test_video_frames_follow_scenes_and_skip_repeats(tmp_path):
    video = tmp_path / "scenes.mp4"
    subprocess.run([
        FFMPEG, "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=red:s=320x240:d=4:r=10", "-f", "lavfi", "-i", "color=blue:s=320x240:d=4:r=10",
        "-f", "lavfi", "-i", "testsrc=s=320x240:d=40:r=10",
        "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0", "-pix_fmt", "yuv420p", str(video),
    ], check=True)  # fmt: skip
    frames = extract_frames(video)
    starts = [round(f.start_s) for f in frames]
    assert starts[:3] == [0, 4, 8]  # every cut, with its timestamp
    assert len(frames) == 3  # the 30 s re-sample inside the near-static test pattern is a repeat
    assert frames[0].end_s == frames[1].start_s and abs(frames[-1].end_s - 48) < 1


@needs_ffmpeg
def test_silent_video_is_indexed_by_frames(conn, tmp_path):
    root = tmp_path / "Filmy"
    root.mkdir()
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:d=5:r=10",
                    "-pix_fmt", "yuv420p", str(root / "klip.mp4")], check=True)  # fmt: skip
    scan_root(conn, root)
    stats = run_queue(conn, FakeEmbedder(), image_embedder=FakeImageEmbedder())
    assert stats.skipped == 0 and stats.frames >= 1
    kinds = {r[0] for r in conn.execute("SELECT kind FROM chunks")}
    assert kinds == {"frame"}
    start, end = conn.execute("SELECT start_ms, end_ms FROM chunks").fetchone()
    assert start == 0 and end > 4000


def test_code_like_queries_never_put_photos_first():
    from lantern.search.hybrid import looks_like_code

    for q in (
        "def add_two(x, y):",
        "backoff_wrapper",
        "parseUserToken",
        "config.toml",
        "src/main.rs",
        "ConnectionRefusedError",
    ):
        assert looks_like_code(q), q
    for q in ("pies", "dziecko w wodzie", "zachód słońca", "README instalacja", "samochód na parkingu"):
        assert not looks_like_code(q), q


def test_transparent_images_are_flattened_onto_white(tmp_path):
    """Black text on a transparent background (PDF exports, graphics) must stay readable, not turn black on black."""
    from PIL import Image, ImageDraw

    from lantern.extractors.image import load_image

    im = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle((40, 80, 260, 120), fill=(0, 0, 0, 255))  # "text"
    im.save(tmp_path / "export.png")
    loaded = load_image(tmp_path / "export.png")
    assert loaded.getpixel((5, 5)) == (255, 255, 255) and loaded.getpixel((150, 100)) == (0, 0, 0)

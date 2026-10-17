"""Thumbnails for clients without folder access (lantern/thumbnails.py): only photos and video frames, user-only,
reused while fresh and deleted after their time."""

import os
import stat
import time

from PIL import Image

from lantern import thumbnails


def test_photo_thumbnail_is_small_private_and_reused(tmp_path):
    photo = tmp_path / "beach.png"
    Image.new("RGB", (1200, 800), (40, 120, 200)).save(photo)
    cache = tmp_path / "cache"
    out = thumbnails.make(str(photo), "image", None, size=200, folder=cache)
    assert out is not None and out.parent == cache
    with Image.open(out) as im:
        assert max(im.size) == 200
    assert stat.S_IMODE(os.stat(cache).st_mode) == 0o700 and stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert thumbnails.make(str(photo), "image", None, size=200, folder=cache) == out  # same file, same thumbnail


def test_no_thumbnail_for_text_or_missing_files(tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("hello")
    assert thumbnails.make(str(note), "text", None, folder=tmp_path / "c") is None
    assert thumbnails.make(str(tmp_path / "gone.png"), "image", None, folder=tmp_path / "c") is None


def test_old_thumbnails_are_deleted(tmp_path):
    old, new = tmp_path / "old.jpg", tmp_path / "new.jpg"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    past = time.time() - thumbnails.THUMBNAIL_TTL_S - 5
    os.utime(old, (past, past))
    thumbnails.prune(tmp_path)
    assert not old.exists() and new.exists()
    thumbnails.prune(tmp_path, max_age_s=0)
    assert not new.exists()

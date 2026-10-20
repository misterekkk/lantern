"""Phase 16, time and place: dates and GPS from file headers, the offline city table, the PL/EN time parser, filters
and browsing in search, and an upgrade that fills dates without re-indexing anything."""

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from lantern import filemeta, places
from lantern.embedding.fake import FakeEmbedder, FakeImageEmbedder
from lantern.extractors import metadata
from lantern.extractors.media import find_ffmpeg
from lantern.indexer import run_queue
from lantern.scanner.diff import scan_root
from lantern.search import hybrid, when
from lantern.search.hybrid import search
from lantern.store import db

try:
    FFMPEG = find_ffmpeg()
except Exception:  # pragma: no cover
    FFMPEG = None
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")
needs_sips = pytest.mark.skipif(shutil.which("sips") is None, reason="macOS sips not available")
NOW = datetime(2026, 10, 19, 15, 0)
KRAKOW, PARIS = (50.0614, 19.9366), (48.8534, 2.3488)
KRAKOW_ID, PARIS_ID = 3094802, 2988507


def _dms(value: float) -> tuple[float, float, float]:
    d = int(value)
    m = int((value - d) * 60)
    return float(d), float(m), round(((value - d) * 60 - m) * 60, 4)


def photo(path: Path, taken: str | None = None, gps: tuple[float, float] | None = None, color=(200, 30, 30)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (320, 240), color)
    for x in range(0, 320, 40):
        img.paste((255, 255, 255), (x, 0, x + 5, 240))
    exif = Image.Exif()
    if taken:
        exif.get_ifd(0x8769)[0x9003] = taken
    if gps:
        g = exif.get_ifd(0x8825)
        g[1], g[2] = ("N" if gps[0] >= 0 else "S"), _dms(abs(gps[0]))
        g[3], g[4] = ("E" if gps[1] >= 0 else "W"), _dms(abs(gps[1]))
    img.save(path, exif=exif)
    return path


def set_mtime(path: Path, when_: datetime) -> None:
    t = when_.timestamp()
    os.utime(path, (t, t))


# ---------- dates in names and plausibility ----------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("IMG_20230514_123456", datetime(2023, 5, 14, 12, 34, 56)),
        ("PXL_20230514_101010123", datetime(2023, 5, 14)),  # prefix + date; the time block is longer than HHMMSS
        ("Screenshot 2023-05-14 at 12.34.56", datetime(2023, 5, 14, 12, 34, 56)),
        ("Zrzut ekranu 2024-01-02 o 09.15.00", datetime(2024, 1, 2, 9, 15, 0)),
        ("faktura 14.05.2023", datetime(2023, 5, 14)),
        ("WhatsApp Image 2023-05-14 at 12.34.56", datetime(2023, 5, 14, 12, 34, 56)),
        ("2019_07_04 wakacje", datetime(2019, 7, 4)),
        ("signal-20220101-101010", datetime(2022, 1, 1)),
    ],
)
def test_dates_in_names(name, expected):
    found = metadata.date_in_name(name, NOW)
    assert found is not None and found.date() == expected.date()
    if expected.hour:
        assert found == expected


@pytest.mark.parametrize(
    "name",
    [
        "report 1950", "invoice 20231234", "order 20230514", "id_19991231", "scan 2023-13-01", "2023-02-30 notes",
        "build 2031-01-01", "photo 1970-01-01", "camera 2000-01-01 00.00.00", "1985-06-01 old", "v2.0.1",
        "12345678", "IMG_1234",
    ],
)  # fmt: skip
def test_numbers_that_only_look_like_dates_are_not_dates(name):
    assert metadata.date_in_name(name, NOW) is None


def test_plausible_dates_only():
    assert metadata.plausible(datetime(2023, 5, 14), NOW)
    assert not metadata.plausible(datetime(1970, 1, 1), NOW)
    assert not metadata.plausible(datetime(2000, 1, 1, 0, 0, 0), NOW)  # camera clock reset
    assert metadata.plausible(datetime(2000, 1, 1, 12, 30, 0), NOW)  # a real moment of that day
    assert not metadata.plausible(datetime(1989, 12, 31), NOW)
    assert not metadata.plausible(datetime(2026, 10, 25), NOW)


def test_folder_name_dates_a_photo_without_one(tmp_path):
    p = photo(tmp_path / "2019-07-04 Wakacje" / "IMG_1234.jpg")
    assert metadata.date_from_path(p, NOW) == datetime(2019, 7, 4)


# ---------- EXIF, HEIC, QuickTime ----------


def test_exif_date_and_gps(tmp_path):
    p = photo(tmp_path / "a.jpg", "2023:05:14 12:34:56", KRAKOW)
    meta = metadata.read(p, "image", now=NOW)
    assert meta.source == "exif" and metadata.from_seconds(meta.date) == datetime(2023, 5, 14, 12, 34, 56)
    assert abs(meta.lat - KRAKOW[0]) < 1e-3 and abs(meta.lon - KRAKOW[1]) < 1e-3
    assert metadata.read(p, "image", places=False, now=NOW).lat is None  # the setting off: no coordinates


def test_reset_exif_date_falls_back_to_the_name_then_the_file_system(tmp_path):
    named = photo(tmp_path / "IMG_20230514_101112.jpg", "2000:01:01 00:00:00")
    meta = metadata.read(named, "image", now=NOW)
    assert meta.source == "name" and metadata.from_seconds(meta.date) == datetime(2023, 5, 14, 10, 11, 12)
    plain = photo(tmp_path / "plain.jpg", "0000:00:00 00:00:00")
    set_mtime(plain, datetime(2021, 3, 4, 5, 6, 7))
    meta = metadata.read(plain, "image", now=NOW)
    assert meta.source == "fs" and metadata.from_seconds(meta.date).date() <= datetime(2021, 3, 4).date()


@needs_sips
def test_heic_exif_is_read_from_its_meta_box(tmp_path):
    jpg = photo(tmp_path / "src.jpg", "2022:08:01 18:00:00", PARIS)
    heic = tmp_path / "IMG_0001.HEIC"
    subprocess.run(["sips", "-s", "format", "heic", str(jpg), "--out", str(heic)], check=True, capture_output=True)
    meta = metadata.read(heic, "image", now=NOW)
    assert meta.source == "exif" and metadata.from_seconds(meta.date) == datetime(2022, 8, 1, 18, 0, 0)
    assert abs(meta.lat - PARIS[0]) < 1e-3


@needs_ffmpeg
def test_quicktime_dates_and_locations(tmp_path):
    base = [FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1", "-c:v", "libx264",
            "-pix_fmt", "yuv420p"]  # fmt: skip
    mov = tmp_path / "phone.mov"  # an iPhone's keys: local creation date with its offset, ISO 6709 location
    subprocess.run([*base, "-movflags", "use_metadata_tags",
                    "-metadata", "com.apple.quicktime.creationdate=2023-05-14T12:34:56+0200",
                    "-metadata", "com.apple.quicktime.location.ISO6709=+48.8534+002.3488+035.000/", str(mov)],
                   check=True)  # fmt: skip
    meta = metadata.read(mov, "video", now=NOW)
    assert meta.source == "quicktime" and metadata.from_seconds(meta.date) == datetime(2023, 5, 14, 12, 34, 56)
    assert abs(meta.lat - PARIS[0]) < 1e-3 and abs(meta.lon - PARIS[1]) < 1e-3
    mp4 = tmp_path / "clip.mp4"  # mvhd (UTC) and a 3GPP location
    subprocess.run([*base, "-metadata", "creation_time=2021-06-01T10:00:00Z", "-metadata",
                    "location=+50.0614+019.9366/", str(mp4)], check=True)  # fmt: skip
    meta = metadata.read(mp4, "video", now=NOW)
    utc = datetime(2021, 6, 1, 10, 0, 0)
    local = datetime.fromtimestamp((utc - datetime(1970, 1, 1)).total_seconds())
    assert meta.source == "quicktime" and metadata.from_seconds(meta.date) == local
    assert abs(meta.lat - KRAKOW[0]) < 1e-3


def test_damaged_files_never_break_the_cascade(tmp_path):
    bad = tmp_path / "broken.mov"
    bad.write_bytes(b"\x00\x00\x00\x10moov\xff\xff\xff\xff\x00\x00\x00\x01")
    set_mtime(bad, datetime(2020, 2, 2))
    assert metadata.read(bad, "video", now=NOW).source == "fs"
    (tmp_path / "broken.heic").write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 40)
    assert metadata.read(tmp_path / "broken.heic", "image", now=NOW).source in ("fs", None)


def test_icloud_placeholders_are_not_read(tmp_path, monkeypatch):
    p = photo(tmp_path / "IMG_20230514_101112.jpg", "2019:01:01 10:00:00")
    monkeypatch.setattr(metadata, "is_dataless", lambda st: True)
    monkeypatch.setattr(metadata, "read_exif", lambda *a: pytest.fail("read a file that is only in iCloud"))
    assert metadata.read(p, "image", now=NOW).source == "name"  # the name and stat only


# ---------- places ----------


def test_nearest_city_offline():
    assert places.nearest(*KRAKOW) == KRAKOW_ID
    assert places.nearest(50.08, 19.92) == KRAKOW_ID  # a few km away
    assert places.nearest(*PARIS) == PARIS_ID
    assert places.nearest(-40.0, -30.0) is None  # the South Atlantic: no city within reach


def test_place_names_in_any_language_and_inflected(conn):
    with db.transaction(conn):
        places.store(conn, places.load({KRAKOW_ID, PARIS_ID}))
    for query, hard in [("zdjęcia z Krakowa", True), ("w Krakowie", True), ("photos in Kraków", True),
                        ("Bilder aus Krakau", True), ("krakow rynek", False), ("wakacje w Paryżu", True),
                        ("Paris trip", False)]:  # fmt: skip
        m = places.find_in_query(conn, query)
        assert m is not None and m.hard == hard, query
    assert places.find_in_query(conn, "kraken octopus") is None
    assert places.find_in_query(conn, "parametry") is None
    row = conn.execute("SELECT name, display FROM places WHERE id = ?", (KRAKOW_ID,)).fetchone()
    assert places.display_name(row[0], row[1], "de") == "Krakau"
    assert places.display_name(row[0], row[1], "pl") == "Kraków"
    assert places.display_name(row[0], row[1], "xx") == row[0]


# ---------- the time parser ----------


@pytest.mark.parametrize(
    "query, rest, hard, first",
    [
        ("zdjęcia z maja 2023", "zdjęcia", True, ("2023-05-01", "2023-06-01")),
        ("photos from last summer", "photos", True, ("2026-06-01", "2026-09-01")),
        ("faktura wczoraj", "faktura", True, ("2026-10-18", "2026-10-19")),
        ("dokumenty w zeszłym tygodniu", "dokumenty", True, ("2026-10-12", "2026-10-19")),
        ("w 2024 roku umowa", "umowa", True, ("2024-01-01", "2025-01-01")),
        ("14 maja 2023", "", True, ("2023-05-14", "2023-05-15")),
        ("May 14, 2023 receipt", "receipt", True, ("2023-05-14", "2023-05-15")),
        ("spotkanie 14.05.2023", "spotkanie", True, ("2023-05-14", "2023-05-15")),
        ("zeszłej zimy narty", "narty", True, ("2025-12-01", "2026-03-01")),
        ("latem 2022 plaża", "plaża", True, ("2022-06-01", "2022-09-01")),
        ("last year taxes", "taxes", True, ("2025-01-01", "2026-01-01")),
        ("wakacje 2023", "wakacje 2023", False, ("2023-01-01", "2024-01-01")),  # a bare year: a boost only
    ],
)
def test_time_phrases(query, rest, hard, first):
    got_rest, tf = when.parse(query, NOW)
    assert got_rest == rest and tf is not None and tf.hard == hard
    a, b = tf.ranges[0]
    assert (metadata.from_seconds(a).date().isoformat(), metadata.from_seconds(b).date().isoformat()) == first


def test_months_without_a_year_and_words_that_are_not_dates():
    assert when.parse("zdjęcia z maja", NOW)[1].months == {5}
    assert when.parse("sierpień", NOW)[1].hard is False
    for q in ("files I may need", "march forward", "jan kowalski", "error 1950", "raport 2031", "fall damage"):
        assert when.parse(q, NOW)[1] is None, q


# ---------- search ----------


def _index_photos(conn, root: Path) -> None:
    photo(root / "maj.jpg", "2023:05:14 12:00:00", KRAKOW, color=(200, 30, 30))
    photo(root / "sierpien.jpg", "2023:08:01 12:00:00", PARIS, color=(30, 200, 30))
    photo(root / "stare.jpg", "2019:05:20 12:00:00", None, color=(30, 30, 200))
    scan_root(conn, root)
    run_queue(conn, FakeEmbedder(), image_embedder=FakeImageEmbedder(), image_text=False)
    while filemeta.refresh(conn):
        pass


def test_browsing_by_time_and_place(conn, embedder, tmp_path):
    _index_photos(conn, tmp_path / "zdjecia")
    may = search(conn, embedder, "z maja 2023", log=False, lang="pl")
    assert [Path(p.path).name for p in may.photos] == ["maj.jpg"]
    assert may.photos[0].date == "2023-05-14" and may.photos[0].place == "Kraków" and may.photos[0].country == "PL"
    mays = search(conn, embedder, "z maja", log=False)  # May of any year, newest first
    assert [Path(p.path).name for p in mays.photos] == ["maj.jpg", "stare.jpg"]
    download = photo(tmp_path / "zdjecia" / "diagram.png")  # dated only by the disk, newer than both
    set_mtime(download, datetime(2023, 5, 30))
    scan_root(conn, tmp_path / "zdjecia")
    run_queue(conn, FakeEmbedder(), image_embedder=FakeImageEmbedder(), image_text=False)
    filemeta.refresh(conn)
    may = search(conn, embedder, "zdjęcia z maja 2023", log=False)
    assert [Path(p.path).name for p in may.photos] == ["maj.jpg", "diagram.png"]  # the camera's photo first
    paris = search(conn, embedder, "zdjęcia z Paryża", log=False, lang="de")
    assert [Path(p.path).name for p in paris.photos] == ["sierpien.jpg"] and paris.photos[0].place == "Paris"
    assert not search(conn, embedder, "z maja 2023", log=False, category="docs").photos
    assert not search(conn, embedder, "in:zdjecia", log=False).photos  # a folder alone is still not a query


def test_a_time_phrase_filters_a_text_query(conn, embedder, tmp_path):
    from conftest import write

    root = tmp_path / "docs"
    old = write(root, "umowa-stara.txt", "Umowa najmu mieszkania, czynsz i kaucja.")
    new = write(root, "umowa-nowa.txt", "Umowa najmu mieszkania, czynsz i kaucja oraz media.")
    set_mtime(old, datetime(2021, 4, 1))
    set_mtime(new, datetime(2024, 4, 1))
    scan_root(conn, root)
    run_queue(conn, embedder)
    filemeta.refresh(conn)
    every = search(conn, embedder, "umowa najmu", log=False)
    assert {Path(r.path).name for r in every.results} == {"umowa-stara.txt", "umowa-nowa.txt"}
    in_2024 = search(conn, embedder, "umowa najmu z 2024", log=False)
    assert [Path(r.path).name for r in in_2024.results] == ["umowa-nowa.txt"]
    assert in_2024.results[0].date == "2024-04-01" and in_2024.results[0].date_source == "fs"
    browse = search(conn, embedder, "dokumenty z 2021", log=False)  # a kind word and a time: browsing that kind
    assert [Path(r.path).name for r in browse.results] == ["umowa-stara.txt"] and not browse.photos


def test_switching_places_off_deletes_coordinates(conn, embedder, tmp_path):
    _index_photos(conn, tmp_path / "zdjecia")
    assert conn.execute("SELECT count(*) FROM places").fetchone()[0] == 2
    filemeta.forget_places(conn)
    assert conn.execute("SELECT count(*) FROM files WHERE lat IS NOT NULL OR place_id IS NOT NULL").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM places").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM place_names").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM files WHERE inferred_date IS NOT NULL").fetchone()[0] == 3  # dates stay
    filemeta.read_places_again(conn)
    while filemeta.refresh(conn):
        pass
    assert conn.execute("SELECT count(*) FROM places").fetchone()[0] == 2


def test_deleting_the_last_photo_of_a_city_deletes_the_city(conn, tmp_path):
    root = tmp_path / "zdjecia"
    _index_photos(conn, root)
    (root / "sierpien.jpg").unlink()
    scan_root(conn, root)
    assert {r[0] for r in conn.execute("SELECT id FROM places")} == {KRAKOW_ID}


def test_upgrade_fills_dates_without_reindexing(lantern_home, tmp_path):
    """An index from 0.1.x (schema 13) keeps every chunk and vector; dates come from file headers only."""
    path = lantern_home / "old.db"
    conn = db.connect(path)
    root = tmp_path / "zdjecia"
    photo(root / "maj.jpg", "2023:05:14 12:00:00", KRAKOW)
    from conftest import write

    write(root, "notatka.txt", "Notatki z podróży do Krakowa.")
    scan_root(conn, root)
    embedder = FakeEmbedder()
    run_queue(conn, embedder, image_embedder=FakeImageEmbedder(), image_text=False)
    chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    vectors = conn.execute("SELECT count(*) FROM vec_chunks_1").fetchone()[0]
    for index in ("files_date", "files_place", "files_country", "files_meta", "files_people"):  # back to schema 13
        conn.execute(f"DROP INDEX {index}")
    for column in ("inferred_date", "date_source", "lat", "lon", "place_id", "country", "meta_version", "people",
                   "women", "men", "face_area", "people_version"):  # fmt: skip
        conn.execute(f"ALTER TABLE files DROP COLUMN {column}")
    conn.execute("DROP TABLE place_names")
    conn.execute("DROP TABLE places")
    conn.execute("PRAGMA user_version = 13")
    conn.close()

    conn = db.connect(path)  # the upgrade
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("SELECT count(*) FROM chunks").fetchone()[0] == chunks
    assert conn.execute("SELECT count(*) FROM vec_chunks_1").fetchone()[0] == vectors
    assert conn.execute("SELECT count(*) FROM jobs WHERE status = 'queued'").fetchone()[0] == 0
    assert filemeta.pending(conn) == 2
    while filemeta.refresh(conn):
        pass
    before = embedder.texts_embedded
    stats = run_queue(conn, embedder, image_embedder=FakeImageEmbedder(), image_text=False)
    assert stats.indexed == 0 and stats.stale == 0 and embedder.texts_embedded == before  # nothing re-embedded
    row = conn.execute("SELECT inferred_date, date_source, place_id FROM files WHERE path LIKE '%maj.jpg'").fetchone()
    assert metadata.from_seconds(row[0]) == datetime(2023, 5, 14, 12, 0, 0) and row[1] == "exif"
    assert row[2] == KRAKOW_ID
    conn.close()


def test_a_changed_or_moved_file_is_read_again(conn, tmp_path):
    root = tmp_path / "zdjecia"
    p = photo(root / "a.jpg", "2023:05:14 12:00:00")
    scan_root(conn, root)
    filemeta.refresh(conn)
    assert filemeta.pending(conn) == 0
    photo(p, "2022:01:02 12:00:00")
    set_mtime(p, datetime(2025, 1, 1))
    scan_root(conn, root)
    assert filemeta.pending(conn) == 1
    filemeta.refresh(conn)
    date = conn.execute("SELECT inferred_date FROM files").fetchone()[0]
    assert metadata.from_seconds(date) == datetime(2022, 1, 2, 12, 0, 0)


# ---------- countries (a camera without GPS, folders named after a country) ----------


def test_countries_in_folder_names():
    assert places.country_in_path(Path("/Users/x/Desktop/Portugalia/IMG_6794.JPG")) == "PT"
    assert places.country_in_path(Path("/Users/x/Wakacje w Portugalii 2026/dzien 1/IMG_1.JPG")) == "PT"
    assert places.country_in_path(Path("/Users/x/Pictures/Japan_2019/DSC_1.jpg")) == "JP"
    assert places.country_in_path(Path("/Users/x/Projekty/mobile/screens/a.png")) is None
    assert places.country_in_path(Path("/Users/x/Downloads/IMG_1.JPG")) is None


def test_a_camera_photo_without_gps_gets_its_folders_country_and_a_screenshot_does_not(conn, embedder, tmp_path):
    root = tmp_path / "Desktop"
    photo(root / "Portugalia" / "IMG_6794.JPG", "2026:02:18 14:06:41", None)  # Canon EOS R10: no coordinates
    shot = photo(root / "Portugalia" / "mapa.png")  # no EXIF date: a graphic, not a photo from a camera
    set_mtime(shot, datetime(2026, 2, 20))
    scan_root(conn, root)
    run_queue(conn, FakeEmbedder(), image_embedder=FakeImageEmbedder(), image_text=False)
    while filemeta.refresh(conn):
        pass
    rows = dict(conn.execute("SELECT path, country FROM files").fetchall())
    assert rows[str(root / "Portugalia" / "IMG_6794.JPG")] == "PT" and rows[str(shot)] is None
    for query in ("zdjęcia z Portugalii", "photos in Portugal", "w Portugalii"):
        found = search(conn, embedder, query, log=False, lang="pl")
        assert [Path(p.path).name for p in found.photos] == ["IMG_6794.JPG"], query
        assert found.photos[0].country == "PT" and found.photos[0].place is None
    filemeta.forget_places(conn)
    assert conn.execute("SELECT count(*) FROM files WHERE country IS NOT NULL").fetchone()[0] == 0


# ---------- people in photos ----------

from lantern import people as people_mod  # noqa: E402
from lantern.search import people as people_query  # noqa: E402


@pytest.mark.parametrize(
    "query, sql",
    [
        ("zdjęcie kobiety", "(f.women >= 1)"),
        ("mężczyźni na plaży", "(f.men >= 1)"),
        ("photos of women", "(f.women >= 1)"),
        ("zdjęcia z ludźmi", "(f.people >= 1)"),
        ("bez ludzi", "(f.people = 0)"),
        ("landscape without people", "(f.people = 0)"),
        ("grupa znajomych", "(f.people >= 3)"),
        ("selfie", "(f.people = 1 AND f.face_area >= 0.03)"),
        ("kobieta i mężczyzna", "(f.women >= 1) AND (f.men >= 1)"),
        ("dwie osoby", "(f.people = ?)"),
        ("two women", "(f.women >= ?)"),
    ],
)
def test_people_words(query, sql):
    found = people_query.parse(query)
    assert found is not None and found.sql == sql


def test_words_that_are_not_about_people():
    for q in ("umowa najmu", "people.py", "manual", "personal", "grupowanie danych"):
        assert people_query.parse(q) is None, q


def test_people_are_counted_and_classified_in_the_worker(conn, tmp_path, monkeypatch):
    root = tmp_path / "zdjecia"
    photo(root / "para.jpg", "2024:06:01 12:00:00")
    photo(root / "pusto.jpg", "2024:06:02 12:00:00")
    scan_root(conn, root)
    run_queue(conn, FakeEmbedder(), image_embedder=FakeImageEmbedder(), image_text=False)
    boxes = {"para.jpg": (2, 0.05, [(0.1, 0.1, 0.3, 0.8), (0.6, 0.1, 0.3, 0.8)]), "pusto.jpg": (0, 0.0, [])}
    monkeypatch.setattr(people_mod, "detect", lambda path: boxes[Path(path).name])
    monkeypatch.setattr(people_mod.Classifier, "__call__", lambda self, crops: (1, len(crops) - 1))
    assert people_mod.pending(conn) == 2
    assert people_mod.refresh(conn, FakeEmbedder()) == 2 and people_mod.pending(conn) == 0
    query = "SELECT path, people, women, men, face_area FROM files"
    rows = {Path(r[0]).name: tuple(r[1:]) for r in conn.execute(query)}
    assert rows == {"para.jpg": (2, 1, 1, 0.05), "pusto.jpg": (0, 0, 0, 0.0)}
    people_mod.forget(conn)
    assert people_mod.pending(conn) == 2
    assert conn.execute("SELECT count(*) FROM files WHERE people IS NOT NULL OR women IS NOT NULL").fetchone()[0] == 0


def test_vision_detection_runs_on_a_photo_without_people(tmp_path):
    """Real Vision, in its own process: PyObjC must never be loaded where the daemon's tests run."""
    import sys

    path = photo(tmp_path / "pasy.jpg")
    code = f"from lantern import people; print(people.detect({str(path)!r}))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    if "No module named 'Vision'" in out.stderr:
        pytest.skip("Vision (PyObjC) not installed")
    assert out.stdout.strip() == "(0, 0.0, [])", out.stderr


def _people_index(conn, root: Path) -> None:
    """Six photos in two places and years with their people already counted."""
    rows = [
        ("kobieta_krk.jpg", "2023:05:01 12:00:00", KRAKOW, (1, 1, 0, 0.06)),
        ("mezczyzna_krk.jpg", "2023:05:02 12:00:00", KRAKOW, (1, 0, 1, 0.01)),
        ("kobieta_paris.jpg", "2024:07:01 12:00:00", PARIS, (1, 1, 0, 0.02)),
        ("grupa_paris.jpg", "2024:07:02 12:00:00", PARIS, (5, 2, 2, 0.004)),
        ("pejzaz_krk.jpg", "2023:05:03 12:00:00", KRAKOW, (0, 0, 0, 0.0)),
        ("pejzaz_paris.jpg", "2024:07:03 12:00:00", PARIS, (0, 0, 0, 0.0)),
    ]
    for i, (name, taken, gps, _) in enumerate(rows):
        photo(root / name, taken, gps, color=(40 * i, 200 - 30 * i, 90))
    scan_root(conn, root)
    run_queue(conn, FakeEmbedder(), image_embedder=FakeImageEmbedder(), image_text=False)
    while filemeta.refresh(conn):
        pass
    with db.transaction(conn):
        for name, _, _, (n, w, m, face) in rows:
            conn.execute("UPDATE files SET people = ?, women = ?, men = ?, face_area = ?, people_version = ? "
                         "WHERE path = ?", (n, w, m, face, people_mod.PEOPLE_VERSION, str(root / name)))  # fmt: skip


@pytest.mark.parametrize(
    "query, expected",
    [
        ("zdjęcia kobiet", {"kobieta_krk.jpg", "kobieta_paris.jpg", "grupa_paris.jpg"}),
        ("zdjęcie kobiety z 2024 w Paryżu", {"kobieta_paris.jpg", "grupa_paris.jpg"}),
        ("mężczyzna w Krakowie", {"mezczyzna_krk.jpg"}),
        ("zdjęcia bez ludzi z 2023", {"pejzaz_krk.jpg"}),
        ("grupa ludzi", {"grupa_paris.jpg"}),
        ("selfie", {"kobieta_krk.jpg"}),
        ("kobieta i mężczyzna", {"grupa_paris.jpg"}),
        ("photos of men in Paris", {"grupa_paris.jpg"}),
    ],
)
def test_people_mix_with_time_and_place(conn, embedder, tmp_path, query, expected):
    _people_index(conn, tmp_path / "zdjecia")
    found = search(conn, embedder, query, log=False, lang="pl")
    assert {Path(p.path).name for p in found.photos} == expected


def test_a_people_word_never_hides_documents(conn, embedder, tmp_path):
    from conftest import write

    _people_index(conn, tmp_path / "zdjecia")
    write(tmp_path / "docs", "prawa-kobiet.txt", "Prawa kobiet w historii: kobieta, równość i głosowanie.")
    scan_root(conn, tmp_path / "docs")
    run_queue(conn, embedder)
    found = search(conn, embedder, "prawa kobiet historia", log=False)
    assert "prawa-kobiet.txt" in {Path(r.path).name for r in found.results}
    assert all(Path(p.path).name in {"kobieta_krk.jpg", "kobieta_paris.jpg", "grupa_paris.jpg"} for p in found.photos)


def test_people_words_without_counted_photos_search_by_meaning(conn, embedder, tmp_path):
    """The setting off or photos not counted yet: "kobieta" must not empty the photos section."""
    _people_index(conn, tmp_path / "zdjecia")
    people_mod.forget(conn)
    found = search(conn, embedder, "kobieta z 2023", log=False, category="photos")
    assert found.photos and {Path(p.path).name for p in found.photos} <= {"kobieta_krk.jpg", "mezczyzna_krk.jpg",
                                                                          "pejzaz_krk.jpg"}  # fmt: skip


def test_unknown_counts_are_left_out_of_people_filters_only(conn, embedder, tmp_path):
    _people_index(conn, tmp_path / "zdjecia")
    with db.transaction(conn):  # Vision saw nobody, the photo model a person: unknown
        conn.execute("UPDATE files SET people = NULL, women = NULL, men = NULL WHERE path LIKE '%pejzaz_paris%'")
    assert "pejzaz_paris.jpg" not in {Path(p.path).name for p in search(conn, embedder, "bez ludzi", log=False).photos}
    assert "pejzaz_paris.jpg" not in {Path(p.path).name for p in search(conn, embedder, "z ludźmi", log=False).photos}
    paris = search(conn, embedder, "zdjęcia z Paryża", log=False)  # no people words: it is there like any photo
    assert "pejzaz_paris.jpg" in {Path(p.path).name for p in paris.photos}
    assert people_query.parse("zdjęcie nie wiadomo kim z Paryża") is None  # "unknown" is not something to search


def test_switching_off_in_the_cli_without_a_daemon_deletes_the_data(lantern_home, tmp_path, monkeypatch, capsys):
    from lantern import cli

    conn = db.connect(lantern_home / "index.db")
    _people_index(conn, tmp_path / "zdjecia")
    conn.close()
    monkeypatch.setattr(cli, "_daemon", lambda: False)
    for key in ("people", "places"):
        assert cli.main(["config", key, "false"]) == 0
    conn = db.connect(lantern_home / "index.db")
    assert conn.execute("SELECT count(*) FROM files WHERE people IS NOT NULL OR place_id IS NOT NULL "
                        "OR country IS NOT NULL OR lat IS NOT NULL").fetchone()[0] == 0  # fmt: skip
    assert conn.execute("SELECT count(*) FROM places").fetchone()[0] == 0
    conn.close()


def test_place_words_know_every_city_not_only_the_indexed_ones():
    assert places.place_words("women in lisbon") == {2}
    assert places.place_words("kobieta w lizbonie") == {2}  # inflected
    assert places.place_words("men in new york") == {2, 3}
    assert places.place_words("portugal two men") == {0}  # a country
    assert places.place_words("two men") == set()


def test_people_words_with_only_a_place_put_the_photos_first(conn, embedder, tmp_path):
    """"portugal two men": the photos are the answer even when a document has the same words (CHANGELOG.md had
    "men" and "Portugal": its files section outranked the photos for "men" but not for "man")."""
    _people_index(conn, tmp_path / "zdjecia")
    for q in ("paris men", "paris man", "men in lisbon", "porto women"):
        _, filters = hybrid.parse_filters(conn, q)
        assert filters.photo_intent, q
    assert not hybrid.parse_filters(conn, "women in science report")[1].photo_intent  # more than a place: as before
    # the order itself (photos 0.75 over a sure file's 0.72) was checked on a real index: the fake embedders here give
    # no visual match for "paris men" (DECISIONS.md, "Inflections and the photos section")


def test_a_people_filter_keeps_photos_scored_against_all_photos(conn, embedder, tmp_path):
    """The filtered photos are scored against every photo in scope: a pool of one or two (the people asked for)
    made the z-score meaningless and dropped them at random."""
    from lantern.search.basic import image_search
    from lantern.search.scope import Scope

    _people_index(conn, tmp_path / "zdjecia")
    men = Scope("f.men >= 1")
    every = image_search(conn, embedder, "men", 20, "pl", FakeImageEmbedder())
    only = image_search(conn, embedder, "men", 20, "pl", FakeImageEmbedder(), only=men)
    men_files = {"mezczyzna_krk.jpg", "grupa_paris.jpg"}
    assert {Path(h.path).name for h in only} <= men_files
    assert {Path(h.path).name for h in every} & men_files <= {Path(h.path).name for h in only}

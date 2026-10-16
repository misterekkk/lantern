"""PDFs (PDFKit, page by page) and text in images (Apple Vision OCR).

Uses the system frameworks only: no model, no network."""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from lantern.embedding.fake import FakeEmbedder, FakeImageEmbedder
from lantern.extractors import kind_for
from lantern.extractors.pdf import extract_pdf
from lantern.extractors.text import SkipFile
from lantern.extractors.vision import Line, clean, image_text, is_priority
from lantern.indexer import run_queue
from lantern.scanner.diff import scan_root
from lantern.search.hybrid import search


def text_image(path: Path, lines: list[str], size=(1200, 700)) -> Path:
    im = Image.new("RGB", size, (245, 245, 245))
    draw = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=40)
    for i, line in enumerate(lines):
        draw.text((60, 60 + i * 70), line, fill=(20, 20, 20), font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


def photo(path: Path) -> Path:
    im = Image.new("RGB", (800, 600), (90, 140, 200))
    ImageDraw.Draw(im).ellipse((300, 200, 500, 400), fill=(240, 200, 60))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


def text_pdf(path: Path, text: str) -> Path:
    """A minimal one-page PDF with a real text layer (Helvetica)."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


def test_pdf_kind():
    assert kind_for(Path("a/Report.PDF")) == "pdf"


def test_pdf_text_layer_page_by_page(tmp_path):
    pages = extract_pdf(text_pdf(tmp_path / "invoice.pdf", "Invoice FV/2025/10/1187 total 245.30"))
    assert [(p.number, p.ocr) for p in pages] == [(1, False)] and "FV/2025/10/1187" in pages[0].text


def test_scanned_pdf_goes_through_ocr(tmp_path):
    scan = text_image(tmp_path / "scan.png", ["PARKING TICKET", "VIN WVWZZZ1JZXW386752"])
    Image.open(scan).save(tmp_path / "scan.pdf")  # an image-only PDF, like a scanner makes
    pages = extract_pdf(tmp_path / "scan.pdf")
    assert pages[0].ocr and "WVWZZZ1JZXW386752" in pages[0].text.replace(" ", "")
    with pytest.raises(SkipFile):
        extract_pdf(tmp_path / "scan.pdf", ocr=False)  # no text layer and OCR off: nothing to index


def test_ocr_reads_screenshots_and_skips_plain_photos(tmp_path):
    shot = text_image(tmp_path / "IMG_1.png", ["Windows Update", "Error code: 0x80070002", "Try again later"])
    assert "0x80070002" in image_text(shot)
    assert image_text(photo(tmp_path / "IMG_2.png")) == ""  # the gate stops it after the fast pass


def test_priority_names():
    assert is_priority(Path("~/Desktop/Zrzut ekranu 2025-10-14 o 09.12.33.png"))
    assert is_priority(Path("/x/Screenshot 2025-10-14.png")) and is_priority(Path("/x/Scans/receipt.jpg"))
    assert not is_priority(Path("/x/IMG_2041.heic"))


def test_clean_drops_watermarks_and_garbage():
    lines = [
        Line("Faktura VAT nr 12/2025", 0.95, (0.1, 0.8, 0.6, 0.05)),
        Line("Do zapłaty 245,30 zł", 0.9, (0.1, 0.7, 0.5, 0.05)),
        Line("~~ |||| ~~", 0.9, (0.1, 0.5, 0.2, 0.05)),  # garbage
        Line("blurry guess", 0.2, (0.1, 0.4, 0.3, 0.05)),  # low confidence
        Line("Shot on iPhone", 0.9, (0.75, 0.02, 0.2, 0.03)),  # camera watermark in the bottom corner
    ]
    assert clean(lines) == "Faktura VAT nr 12/2025\nDo zapłaty 245,30 zł"


def test_text_in_images_and_pdfs_is_searchable(conn, tmp_path):
    root = tmp_path / "Docs"
    text_image(root / "IMG_9002.png", ["Windows Update", "Error code: 0x80070002", "The system cannot find the file"])
    photo(root / "IMG_2041.png")
    text_pdf(root / "faktura.pdf", "Faktura FV/2025/10/1187 energia elektryczna")
    eg, sig = FakeEmbedder(), FakeImageEmbedder()
    scan_root(conn, root)
    stats = run_queue(conn, eg, image_embedder=sig)
    assert stats.image_texts == 1 and stats.images == 2

    resp = search(conn, eg, "0x80070002", log=False, query_embedder=sig)
    assert resp.photos[0].path.endswith("IMG_9002.png") and resp.photos[0].text_rank == 1
    assert "0x80070002" in resp.photos[0].preview and resp.photos_first
    assert not [r for r in resp.results if r.path.endswith(".png")]  # image text stays in the photos section

    pdf_hit = next(r for r in search(conn, eg, "FV/2025/10/1187", log=False).results if r.path.endswith(".pdf"))
    assert pdf_hit.kind == "pdf" and pdf_hit.page == 1


def test_ocr_can_be_switched_off(conn, tmp_path):
    root = tmp_path / "Docs"
    text_image(root / "IMG_9002.png", ["Error code: 0x80070002", "The system cannot find the file"])
    scan_root(conn, root)
    stats = run_queue(conn, FakeEmbedder(), image_embedder=FakeImageEmbedder(), image_text=False)
    assert stats.image_texts == 0
    assert conn.execute("SELECT COUNT(*) FROM chunks WHERE kind = 'ocr'").fetchone()[0] == 0


def test_ocr_and_pdf_open_no_network_connection(tmp_path, monkeypatch):
    import socket

    attempts = []
    real_connect, real_gai = socket.socket.connect, socket.getaddrinfo
    monkeypatch.setattr(socket.socket, "connect", lambda self, a: (attempts.append(a), real_connect(self, a))[1])
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (attempts.append(a[0]), real_gai(*a, **k))[1])
    image_text(text_image(tmp_path / "IMG_1.png", ["Error code: 0x80070002", "Try again later"]))
    extract_pdf(text_pdf(tmp_path / "a.pdf", "Invoice FV/2025/10/1187"))
    assert attempts == []


def test_text_on_video_frames_is_searchable_at_its_moment(conn, tmp_path):
    import subprocess

    from lantern.extractors.media import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        pytest.skip("ffmpeg not installed")
    slide = text_image(tmp_path / "slide.png", ["Kwartalny raport sprzedazy", "Przychod wzrosl o 18 procent"])
    scene = photo(tmp_path / "scene.png")
    root = tmp_path / "Filmy"
    root.mkdir()
    subprocess.run([
        ffmpeg, "-loglevel", "error", "-y", "-loop", "1", "-t", "4", "-i", str(scene), "-loop", "1", "-t", "4",
        "-i", str(slide), "-filter_complex", "[0]scale=1200:700,setsar=1[a];[1]scale=1200:700,setsar=1[b];"
        "[a][b]concat=n=2:v=1:a=0", "-r", "5", "-pix_fmt", "yuv420p", str(root / "prezentacja.mp4"),
    ], check=True)  # fmt: skip
    eg = FakeEmbedder()
    scan_root(conn, root)
    stats = run_queue(conn, eg, image_embedder=FakeImageEmbedder())
    assert stats.screen_texts == 1  # the slide, once; the plain scene has no text
    hit = next(r for r in search(conn, eg, "raport sprzedazy", log=False).results if r.kind == "screen")
    assert hit.path.endswith("prezentacja.mp4") and 3000 <= hit.start_ms <= 5000
    assert search(conn, eg, "raport sprzedazy", log=False, category="media").results  # the recordings filter

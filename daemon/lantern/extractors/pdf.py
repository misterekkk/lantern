"""PDF text with Apple's PDFKit, page by page; scanned pages (no text layer) go through Vision OCR.

Only the index worker imports this module (PyObjC); the daemon never loads it. Read-only: the PDF is opened, never
saved.
"""

from dataclasses import dataclass
from pathlib import Path

from lantern.extractors.text import SkipFile

MAX_PDF_BYTES = 200 * 1024 * 1024
MAX_PAGES = 1000
MAX_OCR_PAGES = 100  # scanned books: ~0.25 s per page
MAX_CHARS = 2_000_000
MIN_PAGE_CHARS = 25  # less than this on a page: treat it as scanned
OCR_DPI = 200
OCR_MAX_SIDE = 3000


@dataclass(frozen=True)
class PdfPage:
    number: int  # 1-based
    text: str
    ocr: bool  # text recognised from the rendered page


def _render(page):
    """A page as a PIL image on white, ~OCR_DPI."""
    import io

    from PIL import Image
    from Quartz import NSMakeSize, kPDFDisplayBoxMediaBox

    bounds = page.boundsForBox_(kPDFDisplayBoxMediaBox)
    scale = min(OCR_DPI / 72, OCR_MAX_SIDE / max(bounds.size.width, bounds.size.height, 1))
    thumb = page.thumbnailOfSize_forBox_(
        NSMakeSize(bounds.size.width * scale, bounds.size.height * scale), kPDFDisplayBoxMediaBox
    )
    tiff = thumb.TIFFRepresentation()
    with Image.open(io.BytesIO(bytes(tiff))) as im:
        from lantern.extractors.vision import flatten

        return flatten(im)


def extract_pdf(path: Path, ocr: bool = True) -> list[PdfPage]:
    import os

    from Foundation import NSURL
    from Quartz import PDFDocument

    if os.path.getsize(path) > MAX_PDF_BYTES:
        raise SkipFile(f"PDF larger than {MAX_PDF_BYTES // 2**20} MB")
    doc = PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(str(path)))
    if doc is None:
        raise SkipFile("cannot open PDF")
    if doc.isLocked():
        raise SkipFile("password-protected PDF")
    pages, chars, ocr_pages = [], 0, 0
    for index in range(min(doc.pageCount(), MAX_PAGES)):
        page = doc.pageAtIndex_(index)
        text = str(page.string() or "").strip()
        scanned = False
        if len(text) < MIN_PAGE_CHARS and ocr and ocr_pages < MAX_OCR_PAGES:
            from lantern.extractors import vision

            recognised = vision.clean(vision.recognize(_render(page), accurate=True))
            ocr_pages += 1
            if recognised:
                text, scanned = recognised, True
        if text:
            pages.append(PdfPage(index + 1, text, scanned))
            chars += len(text)
            if chars > MAX_CHARS:
                break
    if not pages:
        raise SkipFile("PDF without readable text")
    return pages

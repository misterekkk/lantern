"""Text in images with Apple Vision (on device, part of macOS: no model to download, ~110 MB while running).

A cascade, cheapest first (measurements in DECISIONS.md, "OCR research"):
1. file name / folder: screenshots and scans skip the gate (priority),
2. gate: fast recognition (~10–15 ms); an image needs at least two words of 3+ letters to go on,
3. accurate recognition (25–240 ms) in Polish and English with language correction,
4. clean-up: low-confidence lines, punctuation garbage and camera watermarks ("Shot on iPhone", a date in the bottom
   corner) are dropped.
Images are read from the original file at up to MAX_SIDE (the 1024 px copy used for embeddings makes small screen
text unreadable) and transparency is composited onto white (black text on transparent would read as black).
Only the index worker imports this module (PyObjC + Vision); the daemon never loads it.
"""

import io
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

LANGUAGES = ["pl-PL", "en-US"]
MAX_SIDE = 4096
MIN_CONFIDENCE = 0.5
GATE_MIN_WORDS = 2
_WORD = re.compile(r"[^\W_]{3,}")
_PRIORITY_NAME = re.compile(
    r"screen ?shot|zrzut ekranu|screen ?capture|cleanshot|^scan|^skan|scanned|receipt|paragon", re.I
)
_PRIORITY_DIRS = {"screenshots", "zrzuty ekranu", "scans", "skany", "scanned"}
_WATERMARK = re.compile(r"shot on|iphone|galaxy|pixel|\b\d{1,4}[./:-]\d{1,2}[./:-]\d{1,4}\b", re.I)


@dataclass(frozen=True)
class Line:
    text: str
    confidence: float
    box: tuple[float, float, float, float]  # x, y (from the bottom), width, height; normalised 0–1


def is_priority(path: Path) -> bool:
    """Screenshots and scans: certainly worth reading, so they skip the gate."""
    return bool(_PRIORITY_NAME.search(path.stem)) or any(p.name.lower() in _PRIORITY_DIRS for p in path.parents)


def flatten(im: Image.Image) -> Image.Image:
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return im.convert("RGB")


def load_for_ocr(path: Path) -> Image.Image:
    with Image.open(path) as im:
        im.seek(0)
        im = ImageOps.exif_transpose(im)
        image = flatten(im)
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    return image


def recognize(image: Image.Image, accurate: bool = True) -> list[Line]:
    import Vision
    from Foundation import NSData

    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=95)  # text stays sharp; much faster to encode than PNG
    data = buf.getvalue()
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
        NSData.dataWithBytes_length_(data, len(data)), None
    )
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(
        Vision.VNRequestTextRecognitionLevelAccurate if accurate else Vision.VNRequestTextRecognitionLevelFast
    )
    request.setRecognitionLanguages_(LANGUAGES)
    request.setUsesLanguageCorrection_(accurate)
    ok, _ = handler.performRequests_error_([request], None)
    lines = []
    for obs in (request.results() or []) if ok else []:
        candidates = obs.topCandidates_(1)
        if not candidates:
            continue
        best, box = candidates[0], obs.boundingBox()
        lines.append(Line(str(best.string()), float(best.confidence()),
                          (box.origin.x, box.origin.y, box.size.width, box.size.height)))  # fmt: skip
    return lines


def has_text(lines: list[Line]) -> bool:
    return len(_WORD.findall(" ".join(line.text for line in lines))) >= GATE_MIN_WORDS


def clean(lines: list[Line]) -> str:
    kept = []
    for line in lines:
        text = line.text.strip()
        if line.confidence < MIN_CONFIDENCE or not text:
            continue
        alnum = sum(c.isalnum() for c in text)
        if alnum < 2 or alnum / len(text) < 0.5:  # punctuation and symbol garbage
            continue
        x, y, w, h = line.box
        if y < 0.06 and len(text) <= 30 and _WATERMARK.search(text):  # camera watermark in the bottom strip
            continue
        kept.append(text)
    text = "\n".join(kept)
    return text if len(_WORD.findall(text)) >= GATE_MIN_WORDS else ""


def image_text(path: Path) -> str:
    """Readable text in an image, or "" (most photos). Raises nothing for unreadable files: returns ""."""
    try:
        image = load_for_ocr(path)
    except (OSError, ValueError, Image.DecompressionBombError):
        return ""  # e.g. HEIC that Pillow cannot open: the photo itself was still indexed through sips
    if not is_priority(path) and not has_text(recognize(image, accurate=False)):
        return ""
    return clean(recognize(image, accurate=True))

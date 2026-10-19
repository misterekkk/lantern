"""People in photos: how many (Apple Vision's face, body and upper-body detectors, on device) and, per person, whether
the photo models see a woman or a man (a choice between two prompts on a crop around the person). Measured on COCO:
people found in 93% of photos with people and never in a photo without, the exact count in 80%; woman/man right in
98% of one-person photos (DECISIONS.md, phase 16). A guess from appearance; nothing about who the person is.

Runs only in the index worker (PyObjC, the photo model's vision tower): new photos after indexing, older ones in a
`--people-only` run the daemon starts while the setting is on. Nothing is re-embedded.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from lantern.embedding.base import format_image_query
from lantern.store import db

PEOPLE_VERSION = 1  # bump when detection or classification changes: photos are analysed again (no re-embedding)
MIN_CONFIDENCE = 0.3
MAX_CROPS = 4  # the largest people are classified; a crowd counts, its genders only for these
CROP_PAD_BODY, CROP_PAD_FACE = 0.15, 1.2
PROMPTS = ("a photo of a woman", "a photo of a man")
BATCH = 50


@dataclass(frozen=True)
class People:
    count: int
    women: int
    men: int
    face_area: float  # the largest face as a share of the image (portraits, selfies)


def detect(path: Path) -> tuple[int, float, list[tuple[float, float, float, float]]]:
    """(people, largest face share, person boxes as (x, y, w, h) fractions with y from the top, largest first)."""
    import Vision  # noqa: N813 - PyObjC, worker only
    from Foundation import NSURL

    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(path)), None)
    faces = Vision.VNDetectFaceRectanglesRequest.alloc().init()
    bodies = Vision.VNDetectHumanRectanglesRequest.alloc().init()
    bodies.setUpperBodyOnly_(False)
    upper = Vision.VNDetectHumanRectanglesRequest.alloc().init()
    upper.setUpperBodyOnly_(True)
    ok, _ = handler.performRequests_error_([faces, bodies, upper], None)
    if not ok:
        return 0, 0.0, []

    def boxes(request) -> list[tuple[float, float, float, float]]:
        out = []
        for o in request.results() or []:
            if o.confidence() >= MIN_CONFIDENCE:
                b = o.boundingBox()  # normalised, origin bottom left
                out.append((b.origin.x, 1 - b.origin.y - b.size.height, b.size.width, b.size.height))
        return sorted(out, key=lambda b: -b[2] * b[3])

    f, b, u = boxes(faces), boxes(bodies), boxes(upper)
    count = max(len(f), len(b), len(u))  # people behind others hide their bodies or faces: the largest count wins
    face_area = f[0][2] * f[0][3] if f else 0.0
    # crops from the list that saw the most people; bodies first, faces widened to take in the head and shoulders
    best = max(((b, CROP_PAD_BODY), (u, CROP_PAD_BODY), (f, CROP_PAD_FACE)), key=lambda x: len(x[0]))
    pad = best[1]
    crops = [(x - pad * w, y - pad * h, w * (1 + 2 * pad), h * (1 + 2 * pad)) for x, y, w, h in best[0]]
    return count, face_area, crops


def _crop(image, box: tuple[float, float, float, float]):
    W, H = image.size  # noqa: N806
    x, y, w, h = box
    x0, y0 = max(0, int(x * W)), max(0, int(y * H))
    x1, y1 = min(W, int((x + w) * W)), min(H, int((y + h) * H))
    return image.crop((x0, y0, x1, y1)) if x1 - x0 >= 32 and y1 - y0 >= 32 else None


class Classifier:
    """Woman or man per crop with the indexing model (EmbeddingGemma 2's vision tower; 98% on COCO crops)."""

    def __init__(self, embedder):
        self.embedder = embedder
        self.prompts = embedder.embed_text([format_image_query(p, "en") for p in PROMPTS])

    def __call__(self, crops: list) -> tuple[int, int]:
        if not crops:
            return 0, 0
        scores = self.embedder.embed_images(crops) @ self.prompts.T
        women = int(np.sum(scores[:, 0] > scores[:, 1]))
        return women, len(crops) - women


def analyse(path: Path, classify: Classifier) -> People:
    from lantern.extractors.image import load_image

    count, face_area, boxes = detect(path)
    if not count:
        return People(0, 0, 0, 0.0)
    try:
        image = load_image(path)
    except Exception:  # unreadable now: counted, not classified
        return People(count, 0, 0, face_area)
    crops = [c for c in (_crop(image, b) for b in boxes[:MAX_CROPS]) if c is not None]
    women, men = classify(crops)
    return People(count, women, men, round(face_area, 4))


def pending(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT count(*) FROM files WHERE kind = 'image' AND status = 'indexed' AND people_version < ?",
        (PEOPLE_VERSION,),
    ).fetchone()[0]


def refresh(
    conn: sqlite3.Connection,
    embedder,
    should_stop: Callable[[], bool] = lambda: False,
    progress: Callable[[str, int, int, str], None] | None = None,
) -> int:
    """Analyse every indexed photo whose people are missing or from an older PEOPLE_VERSION. Returns how many."""
    total, done = pending(conn), 0
    if not total:
        return 0
    classify = Classifier(embedder)
    while not should_stop():
        rows = conn.execute(
            "SELECT id, path FROM files WHERE kind = 'image' AND status = 'indexed' AND people_version < ? "
            "ORDER BY id LIMIT ?",
            (PEOPLE_VERSION, BATCH),
        ).fetchall()
        if not rows:
            break
        updates = []
        for row in rows:
            if should_stop():
                break
            done += 1
            if progress:
                progress("people", done, total, row["path"])
            try:
                p = analyse(Path(row["path"]), classify)
                updates.append((p.count, p.women, p.men, p.face_area, PEOPLE_VERSION, row["id"]))
            except Exception:  # one bad file never stops the rest; marked so it is not retried in a loop
                updates.append((None, None, None, None, PEOPLE_VERSION, row["id"]))
        with db.transaction(conn):
            conn.executemany(
                "UPDATE files SET people = ?, women = ?, men = ?, face_area = ?, people_version = ? WHERE id = ?",
                updates,
            )
    embedder.unload_vision()
    return done


def forget(conn: sqlite3.Connection) -> None:
    """The setting switched off: every count and guess disappears from the index."""
    with db.transaction(conn):
        conn.execute("UPDATE files SET people = NULL, women = NULL, men = NULL, face_area = NULL, people_version = 0 "
                     "WHERE people_version > 0")  # fmt: skip

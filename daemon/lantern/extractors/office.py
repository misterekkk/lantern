"""Office documents as plain text: Word, Excel and PowerPoint (docx, xlsx, pptx) read from their ZIP + XML with the
standard library; RTF, old Word (doc) and OpenDocument text (odt) through macOS's own `textutil`. Read-only: the
file is opened, never saved, and nothing leaves the Mac.

- docx: paragraphs of the body, then footnotes and endnotes; tabs and line breaks kept, deleted tracked changes and
  field codes left out.
- xlsx: each sheet's name, then its rows (cells separated by tabs); numbers as stored.
- pptx: slides in presentation order, each slide's text and then its speaker notes; slides separated by a blank line.
Lines are what results point at (`start_line`) and what the preview shows (`api_preview` reads the same text).
"""

import posixpath
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from lantern.extractors.text import SkipFile

MAX_OFFICE_BYTES = 100 * 1024 * 1024  # the file itself (images make documents big; the text is read from XML)
MAX_MEMBER_BYTES = 50 * 1024 * 1024  # one XML part, uncompressed: a ZIP bomb stops here
MAX_TOTAL_BYTES = 200 * 1024 * 1024  # all parts read from one file, uncompressed
MAX_CHARS = 2_000_000  # extracted text, as for PDFs
TEXTUTIL = "/usr/bin/textutil"
TEXTUTIL_TIMEOUT_S = 120

OOXML_SUFFIXES = (".docx", ".xlsx", ".pptx")
TEXTUTIL_SUFFIXES = (".rtf", ".doc", ".odt")

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def office_text(path: Path) -> str:
    """The text of an Office document, or SkipFile when it cannot be read (damaged, encrypted, too large)."""
    if path.stat().st_size > MAX_OFFICE_BYTES:
        raise SkipFile(f"document larger than {MAX_OFFICE_BYTES // 2**20} MB")
    suffix = path.suffix.lower()
    if suffix in TEXTUTIL_SUFFIXES:
        return _textutil(path)
    try:
        with zipfile.ZipFile(path) as z:
            parts = _Parts(z)
            text = {".docx": _docx, ".xlsx": _xlsx, ".pptx": _pptx}[suffix](parts)
    except (zipfile.BadZipFile, ElementTree.ParseError, KeyError, RecursionError, ValueError) as exc:
        # encrypted documents are not ZIP files (an OLE container), and Word's ~$ owner files are not either
        raise SkipFile(f"cannot read the document ({type(exc).__name__})") from None
    return _clip(text)


def _clip(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text[:MAX_CHARS]


class _Parts:
    """XML parts of one OOXML file, with size limits."""

    def __init__(self, z: zipfile.ZipFile):
        self.z = z
        self.names = set(z.namelist())
        self.read_bytes = 0

    def xml(self, name: str) -> ElementTree.Element | None:
        if name not in self.names:
            return None
        info = self.z.getinfo(name)
        self.read_bytes += info.file_size
        if info.file_size > MAX_MEMBER_BYTES or self.read_bytes > MAX_TOTAL_BYTES:
            raise ValueError("document part too large")
        return ElementTree.fromstring(self.z.read(name))

    def rels(self, part: str) -> dict[str, tuple[str, str]]:
        """Relationship id -> (type suffix, target part) of a part ("word/document.xml")."""
        folder, name = posixpath.split(part)
        root = self.xml(posixpath.join(folder, "_rels", name + ".rels"))
        out = {}
        for rel in root.iter(f"{_PKG_REL}Relationship") if root is not None else []:
            if rel.get("TargetMode") == "External":
                continue
            target = posixpath.normpath(posixpath.join(folder, rel.get("Target", "")))
            out[rel.get("Id", "")] = (rel.get("Type", "").rsplit("/", 1)[-1], target)
        return out


def _paragraphs(root: ElementTree.Element, p: str, t: str, breaks: tuple[str, ...], tab: str | None) -> list[str]:
    """Paragraph texts in document order. A paragraph nested in another (a text box) becomes its own line instead of
    being glued to the outer one."""
    out: list[str] = []

    def visit(el: ElementTree.Element, buf: list[str] | None) -> None:
        if el.tag == p:
            mine: list[str] = []
            for child in el:
                visit(child, mine)
            out.append("".join(mine))
            return
        if buf is not None:
            if el.tag == t:
                buf.append(el.text or "")
            elif el.tag == tab:
                buf.append("\t")
            elif el.tag in breaks:
                buf.append("\n")
        for child in el:
            visit(child, buf)

    visit(root, None)
    return out


def _docx(parts: _Parts) -> str:
    if "word/document.xml" not in parts.names:
        raise KeyError("word/document.xml")
    lines = []
    for name in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
        root = parts.xml(name)
        if root is not None:
            lines += _paragraphs(root, f"{_W}p", f"{_W}t", (f"{_W}br", f"{_W}cr"), f"{_W}tab")
    return "\n".join(lines)


def _xlsx(parts: _Parts) -> str:
    shared = []
    strings = parts.xml("xl/sharedStrings.xml")
    for si in strings.iter(f"{_S}si") if strings is not None else []:
        # rich text runs, without the phonetic guide (rPh) that East Asian files add
        shared.append("".join(t.text or "" for r in si if r.tag != f"{_S}rPh" for t in r.iter(f"{_S}t")))
    workbook = parts.xml("xl/workbook.xml")
    if workbook is None:
        raise KeyError("xl/workbook.xml")
    rels = parts.rels("xl/workbook.xml")
    lines: list[str] = []
    chars = 0
    for sheet in workbook.iter(f"{_S}sheet"):
        target = rels.get(sheet.get(f"{_REL}id", ""), ("", ""))[1]
        root = parts.xml(target) if target else None
        if root is None:
            continue
        if lines:
            lines.append("")
        lines.append(sheet.get("name", ""))
        for row in root.iter(f"{_S}row"):
            cells = [_cell(c, shared) for c in row.iter(f"{_S}c")]
            line = "\t".join(cells).rstrip("\t")
            if line:
                lines.append(line)
                chars += len(line)
            if chars > MAX_CHARS:
                return "\n".join(lines)
    return "\n".join(lines)


def _cell(c: ElementTree.Element, shared: list[str]) -> str:
    kind = c.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in c.iter(f"{_S}t"))
    v = c.find(f"{_S}v")
    value = v.text if v is not None and v.text else ""
    if kind == "s" and value.isdigit():
        i = int(value)
        return shared[i] if i < len(shared) else ""
    if kind == "b":
        return {"1": "TRUE", "0": "FALSE"}.get(value, value)
    return value


def _pptx(parts: _Parts) -> str:
    presentation = parts.xml("ppt/presentation.xml")
    if presentation is None:
        raise KeyError("ppt/presentation.xml")
    rels = parts.rels("ppt/presentation.xml")
    slides = []
    for sld in presentation.iter(f"{_P}sldId"):
        kind, target = rels.get(sld.get(f"{_REL}id", ""), ("", ""))
        if kind == "slide":
            slides.append(target)
    blocks = []
    for slide in slides:
        lines = _drawing_text(parts.xml(slide))
        for kind, target in parts.rels(slide).values():
            if kind == "notesSlide":
                lines += _notes_text(parts.xml(target))
        blocks.append("\n".join(line for line in lines if line.strip()))
    return "\n\n".join(blocks)


def _drawing_text(root: ElementTree.Element | None) -> list[str]:
    if root is None:
        return []
    return _paragraphs(root, f"{_A}p", f"{_A}t", (f"{_A}br",), None)


def _notes_text(root: ElementTree.Element | None) -> list[str]:
    """Speaker notes: the notes body only, not the slide image or number placeholders that notes pages also hold."""
    if root is None:
        return []
    lines = []
    for sp in root.iter(f"{_P}sp"):
        ph = sp.find(f".//{_P}nvPr/{_P}ph")
        if ph is not None and ph.get("type") == "body":
            lines += _drawing_text(sp)
    return lines


def _textutil(path: Path) -> str:
    """RTF, .doc and .odt through macOS's textutil (the converter behind TextEdit): local, read-only, plain UTF-8."""
    try:
        done = subprocess.run(
            [TEXTUTIL, "-convert", "txt", "-encoding", "UTF-8", "-stdout", str(path)],
            capture_output=True, timeout=TEXTUTIL_TIMEOUT_S,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SkipFile(f"cannot read the document ({type(exc).__name__})") from None
    if done.returncode != 0:
        raise SkipFile(f"cannot read the document: {done.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return _clip(done.stdout.decode("utf-8", "replace"))

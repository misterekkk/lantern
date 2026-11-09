"""Plain text / source code extraction. Opens files read-only and never writes to them."""

import re
from html.parser import HTMLParser
from pathlib import Path

from charset_normalizer import from_bytes

from lantern.config import MAX_TEXT_FILE_BYTES


class SkipFile(Exception):
    """The file should not be indexed (binary, too large, undecodable)."""


def extract_text(path: Path, max_bytes: int = MAX_TEXT_FILE_BYTES) -> str:
    with open(path, "rb") as f:  # read-only
        data = f.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise SkipFile(f"larger than {max_bytes} bytes")
    if b"\x00" in data[:8192]:
        raise SkipFile("binary content")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        best = from_bytes(data).best()
        if best is None:
            raise SkipFile("unknown text encoding") from None
        text = str(best)
    return text.replace("\r\n", "\n").lstrip("﻿")


HTML_SUFFIXES = (".html", ".htm")


def is_html(path: Path | str) -> bool:
    return str(path).lower().endswith(HTML_SUFFIXES)


def document_text(path: Path, max_bytes: int = MAX_TEXT_FILE_BYTES) -> str:
    """The text Lantern indexes and previews: the file as it is, an HTML page as the text a browser shows."""
    text = extract_text(path, max_bytes)
    return visible_html(text) if is_html(path) else text


# Elements whose content a reader never sees as text.
_HIDDEN = frozenset({"script", "style", "noscript", "template", "svg", "math", "iframe", "object"})
# Elements that separate words: text on both sides is not one word ("<td>a</td><td>b</td>" is "a b").
_BREAKS = frozenset({
    "address", "article", "aside", "blockquote", "br", "caption", "dd", "details", "div", "dl", "dt", "figcaption",
    "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "img", "input", "li", "main", "nav",
    "ol", "option", "p", "pre", "section", "summary", "table", "tbody", "td", "tfoot", "th", "thead", "title", "tr",
    "ul",
})  # fmt: skip
_SPACE = re.compile(r"\s+")


class _Visible(HTMLParser):
    def __init__(self, n_lines: int):
        super().__init__(convert_charrefs=True)
        self.lines: list[list[str]] = [[] for _ in range(n_lines)]
        self.hidden = 0
        self.gap = False

    def handle_starttag(self, tag, attrs):
        if tag in _HIDDEN:
            self.hidden += 1
        self.gap = self.gap or tag in _BREAKS

    def handle_startendtag(self, tag, attrs):
        self.gap = self.gap or tag in _BREAKS

    def handle_endtag(self, tag):
        if tag in _HIDDEN and self.hidden:
            self.hidden -= 1
        self.gap = self.gap or tag in _BREAKS

    def handle_data(self, data):
        if self.hidden:
            return
        line = self.getpos()[0] - 1
        for i, part in enumerate(data.split("\n")):
            row = line + i
            if row >= len(self.lines):
                break
            if i > 0:
                self.gap = True  # a line break in the source separates words, as whitespace does
            if not part.strip():
                self.gap = self.gap or bool(part)
                continue
            words = self.lines[row]
            if words and (self.gap or part[0].isspace()):
                words.append(" ")
            words.append(_SPACE.sub(" ", part.strip()) + (" " if part[-1].isspace() else ""))
            self.gap = False


def visible_html(source: str) -> str:
    """An HTML page as the text a reader sees, on the same lines as in the source (results open at the right line and
    previews show the same text): tags, scripts and styles removed, entities decoded, lines of markup left empty."""
    parser = _Visible(source.count("\n") + 1)
    parser.feed(source)
    parser.close()
    return "\n".join("".join(words).strip() for words in parser.lines)

"""Office documents (extractors/office.py): Word, Excel and PowerPoint from their XML, RTF/doc/odt through textutil."""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from lantern.extractors import kind_for
from lantern.extractors import office as office_mod
from lantern.extractors.office import office_text
from lantern.extractors.text import SkipFile, document_text
from lantern.indexer import OFFICE_PIPELINE_VERSION, run_queue
from lantern.scanner.diff import scan_root
from lantern.scanner.walk import iter_files
from lantern.search.hybrid import search

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
PKG = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def zipped(path: Path, parts: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for name, text in parts.items():
            z.writestr(name, text)
    return path


def rels(*items: tuple[str, str, str]) -> str:
    return (
        f"<Relationships {PKG}>"
        + "".join(f'<Relationship Id="{i}" Type="{TYPE}{t}" Target="{target}"/>' for i, t, target in items)
        + "</Relationships>"
    )


def docx(path: Path) -> Path:
    body = (
        f"<w:document {W}><w:body>"
        "<w:p><w:r><w:t>Umowa najmu lokalu</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Czynsz:</w:t></w:r><w:r><w:tab/><w:t>2 450,00 zł</w:t></w:r></w:p>"
        "<w:p><w:del><w:r><w:delText>stara kwota 1 900 zł</w:delText></w:r></w:del>"
        "<w:r><w:t>Kaucja zwrotna</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Ramka:</w:t></w:r><w:r><w:pict><w:txbxContent><w:p><w:r><w:t>tekst w polu</w:t></w:r></w:p>"
        "</w:txbxContent></w:pict></w:r></w:p>"
        "</w:body></w:document>"
    )
    notes = (
        f"<w:footnotes {W}><w:footnote><w:p><w:r><w:t>Przypis o zwierzętach</w:t></w:r></w:p></w:footnote>"
        "</w:footnotes>"
    )
    return zipped(path, {"word/document.xml": body, "word/footnotes.xml": notes})


def xlsx(path: Path) -> Path:
    workbook = (
        f"<workbook {S} {R}><sheets>"
        '<sheet name="Budżet" sheetId="1" r:id="rId2"/><sheet name="Notatki" sheetId="2" r:id="rId1"/>'
        "</sheets></workbook>"
    )
    strings = (
        f"<sst {S}><si><t>Wydatek</t></si><si><t>Kwota</t></si><si><r><t>Prąd </t></r><r><t>i gaz</t></r></si></sst>"
    )
    budget = (
        f"<worksheet {S}><sheetData>"
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
        '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>187.15</v></c></row>'
        "</sheetData></worksheet>"
    )
    notes = (
        f"<worksheet {S}><sheetData>"
        '<row r="1"><c r="A1" t="inlineStr"><is><t>zapłacić do piątku</t></is></c><c r="B1" t="b"><v>1</v></c></row>'
        "</sheetData></worksheet>"
    )
    return zipped(path, {
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": rels(("rId1", "worksheet", "worksheets/sheet1.xml"),
                                           ("rId2", "worksheet", "worksheets/sheet2.xml")),
        "xl/sharedStrings.xml": strings,
        "xl/worksheets/sheet1.xml": notes,
        "xl/worksheets/sheet2.xml": budget,
    })  # fmt: skip


def slide(text: str) -> str:
    return f"<p:sld {P} {A}><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p>" \
           "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"  # fmt: skip


def pptx(path: Path) -> Path:
    presentation = (
        f'<p:presentation {P} {R}><p:sldIdLst><p:sldId id="256" r:id="rId7"/><p:sldId id="257" r:id="rId3"/>'
        "</p:sldIdLst></p:presentation>"
    )
    notes = (
        f"<p:notes {P} {A}><p:cSld><p:spTree>"
        '<p:sp><p:nvSpPr><p:nvPr><p:ph type="sldNum"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>1</a:t></a:r>'
        "</a:p></p:txBody></p:sp>"
        '<p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>Powiedzieć o kosztach'
        "</a:t></a:r></a:p></p:txBody></p:sp>"
        "</p:spTree></p:cSld></p:notes>"
    )
    return zipped(path, {
        "ppt/presentation.xml": presentation,
        "ppt/_rels/presentation.xml.rels": rels(("rId3", "slide", "slides/slide1.xml"),
                                                ("rId7", "slide", "slides/slide2.xml")),
        "ppt/slides/slide1.xml": slide("Druga strona: podsumowanie"),
        "ppt/slides/slide2.xml": slide("Pierwsza strona: wyniki kwartału"),
        "ppt/slides/_rels/slide2.xml.rels": rels(("rId1", "notesSlide", "../notesSlides/notesSlide1.xml")),
        "ppt/notesSlides/notesSlide1.xml": notes,
    })  # fmt: skip


def test_word_paragraphs_tabs_footnotes_without_deleted_text(tmp_path):
    text = office_text(docx(tmp_path / "umowa.docx"))
    assert text.splitlines()[:3] == ["Umowa najmu lokalu", "Czynsz:\t2 450,00 zł", "Kaucja zwrotna"]
    assert "tekst w polu" in text.splitlines() and "Przypis o zwierzętach" in text
    assert "stara kwota" not in text  # a deleted tracked change is not the document's text


def test_excel_sheets_in_workbook_order_with_their_names(tmp_path):
    text = office_text(xlsx(tmp_path / "budzet.xlsx"))
    assert text.splitlines() == ["Budżet", "Wydatek\tKwota", "Prąd i gaz\t187.15", "", "Notatki",
                                 "zapłacić do piątku\tTRUE"]  # fmt: skip


def test_powerpoint_slides_in_presentation_order_with_notes(tmp_path):
    text = office_text(pptx(tmp_path / "prezentacja.pptx"))
    assert text == "Pierwsza strona: wyniki kwartału\nPowiedzieć o kosztach\n\nDruga strona: podsumowanie"


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("textutil"), reason="macOS textutil")
def test_rtf_through_textutil(tmp_path):
    source = tmp_path / "notatka.txt"
    source.write_text("Spotkanie w Łodzi o dziesiątej\n")
    subprocess.run(["textutil", "-convert", "rtf", str(source), "-output", str(tmp_path / "notatka.rtf")], check=True)
    assert "Spotkanie w Łodzi o dziesiątej" in office_text(tmp_path / "notatka.rtf")


def test_damaged_encrypted_and_oversized_documents_are_skipped(tmp_path, monkeypatch):
    (tmp_path / "zepsuty.docx").write_bytes(b"\xd0\xcf\x11\xe0 not a zip (an encrypted document is an OLE file)")
    with pytest.raises(SkipFile):
        office_text(tmp_path / "zepsuty.docx")
    zipped(tmp_path / "pusty.docx", {"[Content_Types].xml": "<Types/>"})
    with pytest.raises(SkipFile):
        office_text(tmp_path / "pusty.docx")
    monkeypatch.setattr(office_mod, "MAX_MEMBER_BYTES", 100)  # a ZIP bomb's huge part is never decompressed
    with pytest.raises(SkipFile):
        office_text(docx(tmp_path / "duzy.docx"))


def test_office_documents_are_indexed_searched_and_previewed(conn, embedder, tmp_path, lantern_home):
    root = tmp_path / "dokumenty"
    docx(root / "umowa.docx")
    xlsx(root / "budzet.xlsx")
    pptx(root / "prezentacja.pptx")
    zipped(root / "~$umowa.docx", {"x": "Word's owner file of an open document"})
    assert kind_for(root / "umowa.docx") == "text"
    assert sorted(e.path.name for e in iter_files(root)) == ["budzet.xlsx", "prezentacja.pptx", "umowa.docx"]
    scan_root(conn, root)
    stats = run_queue(conn, embedder)
    assert stats.indexed == 3 and stats.failed == 0
    versions = {r[0] for r in conn.execute("SELECT pipeline_version FROM files")}
    assert versions == {OFFICE_PIPELINE_VERSION}
    hits = search(conn, embedder, "kaucja", 10, log=False, lang="pl").results
    assert hits and Path(hits[0].path).name == "umowa.docx"
    hits = search(conn, embedder, "187,15", 10, log=False, lang="pl").results
    assert hits and Path(hits[0].path).name == "budzet.xlsx"
    assert document_text(root / "prezentacja.pptx").startswith("Pierwsza strona")  # what the preview shows
    assert run_queue(conn, embedder).stale == 0

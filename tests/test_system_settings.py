"""System Settings panes and settings as results, from a fake Settings extension (never the real system folder)."""

import plistlib
from pathlib import Path

import pytest

from lantern import system_settings as ss
from lantern.embedding.base import format_document
from lantern.embedding.fake import FakeEmbedder
from lantern.search.hybrid import search
from lantern.search.settings import settings_search
from lantern.store import db


def make_pane(folder: Path, name: str, bundle_id: str, loctable: dict, terms: dict[str, dict], url_ok=True) -> None:
    appex = folder / f"{name}.appex" / "Contents"
    (appex / "Resources").mkdir(parents=True)
    attrs = {"EXExtensionPointIdentifier": ss.SETTINGS_POINT,
             "SettingsExtensionAttributes": {"allowsXAppleSystemPreferencesURLScheme": url_ok,
                                             "searchTermsFileName": name}}  # fmt: skip
    info = {
        "CFBundleIdentifier": bundle_id,
        "EXAppExtensionAttributes": attrs,
        "CFBundleIcons": {"ISGraphicIconConfiguration": {"ISSymbolName": "calendar", "ISEnclosureColor": "Blue"}},
    }
    (appex / "Info.plist").write_bytes(plistlib.dumps(info))
    (appex / "Resources" / "InfoPlist.loctable").write_bytes(plistlib.dumps(loctable, fmt=plistlib.FMT_BINARY))
    for lang, entries in terms.items():
        (appex / "Resources" / f"{lang}.lproj").mkdir()
        data = {k: {"localizableStrings": [{"title": t, "index": idx}]} for k, (t, idx) in entries.items()}
        (appex / "Resources" / f"{lang}.lproj" / f"{name}.searchTerms").write_bytes(plistlib.dumps(data))


@pytest.fixture
def extensions(tmp_path, monkeypatch):
    folder = tmp_path / "Extensions"
    folder.mkdir()
    make_pane(folder, "DateAndTimePref", "com.apple.Date-Time-Settings.extension",
              {"pl": {"CFBundleDisplayName": "Data i czas"}, "en": {"CFBundleDisplayName": "Date & Time"}},
              {"pl": {"24HourTime": ("Używaj formatu 24‑godzinnego na pasku menu", "24, godz, godzina, czas")},
               "en": {"24HourTime": ("Use 24-hour time in the menu bar", "24, hour, clock")}})  # fmt: skip
    make_pane(folder, "Hidden", "com.apple.hidden", {"pl": {"CFBundleDisplayName": "Ukryte"}}, {}, url_ok=False)
    monkeypatch.setenv("LANTERN_SETTINGS_DIR", str(folder))
    return folder


def test_panes_and_settings_in_the_preferred_languages(extensions):
    items = {i.key: i for i in ss.scan_settings(["pl", "en"])}
    assert set(items) == {"com.apple.Date-Time-Settings.extension", "com.apple.Date-Time-Settings.extension/24HourTime"}
    pane = items["com.apple.Date-Time-Settings.extension"]
    assert (pane.title, pane.names, pane.symbol) == ("Data i czas", ["Date & Time"], "calendar")
    setting = items["com.apple.Date-Time-Settings.extension/24HourTime"]
    assert setting.title.startswith("Używaj formatu") and setting.names == ["Use 24-hour time in the menu bar"]
    assert setting.url == "x-apple.systempreferences:com.apple.Date-Time-Settings.extension"


def test_search_by_name_words_and_other_language(conn, extensions):
    embedder = FakeEmbedder()
    model_id = db.ensure_model(conn, embedder.model_info())
    embed = lambda docs: embedder.embed_text([format_document(None, d) for d in docs])  # noqa: E731
    ss.store_settings(conn, ss.scan_settings(["pl", "en"]), ["pl", "en"], embed, model_id)
    assert settings_search(conn, "data i cz")[0].is_pane  # the pane by its name
    assert settings_search(conn, "24 godz")[0].title.startswith("Używaj formatu")  # a setting by its words
    assert settings_search(conn, "hour clock")[0].title.startswith("Używaj formatu")  # by the English words
    resp = search(conn, embedder, "24 godz", log=False)
    assert resp.settings_first and resp.sections()[0][0] == "settings" and "settings" in resp.categories

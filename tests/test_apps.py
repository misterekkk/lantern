"""Applications as results: names as text (exact, prefix, initials, typos, translations, keywords), meaning as a
fallback, and their place in the search response. Uses fake app bundles, never the real /Applications."""

import plistlib
import subprocess
from pathlib import Path

import pytest

from lantern import apps as apps_mod
from lantern.embedding.base import format_document
from lantern.embedding.fake import FakeEmbedder
from lantern.search.apps import app_search, name_match
from lantern.search.hybrid import search
from lantern.store import db


def make_app(folder: Path, name: str, info: dict, loctable: dict | None = None, strings: dict | None = None) -> Path:
    app = folder / f"{name}.app"
    resources = app / "Contents" / "Resources"
    resources.mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    if loctable:
        (resources / "InfoPlist.loctable").write_bytes(plistlib.dumps(loctable, fmt=plistlib.FMT_BINARY))
    for lang, entries in (strings or {}).items():
        (resources / f"{lang}.lproj").mkdir()
        text = "".join(f'"{k}" = "{v}";\n' for k, v in entries.items())
        (resources / f"{lang}.lproj" / "InfoPlist.strings").write_text(text, encoding="utf-16")
    return app


@pytest.fixture
def applications(tmp_path, monkeypatch):
    folder = tmp_path / "Applications"
    folder.mkdir()
    make_app(
        folder,
        "Calculator",
        {"CFBundleName": "Calculator", "CFBundleIdentifier": "com.apple.calculator"},
        loctable={"pl": {"CFBundleDisplayName": "Kalkulator"}, "de": {"CFBundleDisplayName": "Rechner"}},
    )
    make_app(
        folder,
        "System Settings",
        {"CFBundleName": "System Settings", "LSApplicationCategoryType": "public.app-category.utilities"},
        loctable={"pl": {"CFBundleName": "Ustawienia systemowe", "MDItemKeywords": "Preferencje systemowe"}},
    )
    make_app(
        folder,
        "Discord",
        {"CFBundleName": "Discord", "LSApplicationCategoryType": "public.app-category.social-networking"},
    )
    make_app(
        folder,
        "Visual Studio Code",
        {"CFBundleName": "Code", "LSApplicationCategoryType": "public.app-category.developer-tools"},
    )
    make_app(folder, "Google Chrome", {"CFBundleName": "Chrome"}, strings={"pl": {"CFBundleDisplayName": "Chrome PL"}})
    make_app(folder, "Helper", {"CFBundleName": "Helper", "LSBackgroundOnly": True})
    (folder / "Office").mkdir()
    make_app(folder / "Office", "Word", {"CFBundleName": "Microsoft Word"})
    monkeypatch.setenv("LANTERN_APP_DIRS", str(folder))
    monkeypatch.setattr(apps_mod, "spotlight_category", lambda app: None)  # no Spotlight metadata in tests
    return folder


def test_scan_reads_names_in_the_preferred_languages(applications):
    found = {Path(a.path).stem: a for a in apps_mod.scan_apps(["pl", "en"])}
    assert set(found) == {"Calculator", "System Settings", "Discord", "Visual Studio Code", "Google Chrome", "Word"}
    calc = found["Calculator"]  # shown as in a Polish Finder, found by both names; not "Rechner" (no German)
    assert calc.name == "Kalkulator" and calc.names == ["Calculator"]
    assert (
        found["System Settings"].keywords == ["Preferencje systemowe"]
        and found["Discord"].category == "social-networking"
    )
    assert found["Visual Studio Code"].names == ["Code"]  # the bundle name besides the one Finder shows
    if subprocess.run(["which", "plutil"], capture_output=True).returncode == 0:
        assert found["Google Chrome"].name == "Chrome PL"  # old-style UTF-16 .strings, read with plutil


@pytest.mark.parametrize(("typed", "level"), [
    ("kalkulator", "exact"), ("calc", "prefix"), ("studio", "word"), ("vsc", "initials"),
    ("dicsord", "typo"), ("preferencje", "keyword"),
])  # fmt: skip
def test_name_levels(typed, level):
    names = {
        "exact": ["Calculator", "Kalkulator"],
        "prefix": ["Calculator"],
        "word": ["Visual Studio Code"],
        "initials": ["Visual Studio Code"],
        "typo": ["Discord"],
        "keyword": ["System Settings"],
    }[level]
    keywords = ["Preferencje systemowe"] if level == "keyword" else []
    assert name_match(typed, names, keywords)[0] == level


def stored(conn, embedder):
    model_id = db.ensure_model(conn, embedder.model_info())
    found = apps_mod.scan_apps(["pl", "en"])
    embed = lambda docs: embedder.embed_text([format_document(None, d) for d in docs])  # noqa: E731
    return apps_mod.store_apps(conn, found, ["pl", "en"], embed, model_id)


def test_vectors_are_only_recomputed_for_changed_apps(conn, applications):
    embedder = FakeEmbedder()
    assert stored(conn, embedder) == 6
    assert stored(conn, embedder) == 0  # nothing changed
    make_app(applications, "Notes", {"CFBundleName": "Notes"})
    assert stored(conn, embedder) == 1 and conn.execute("SELECT COUNT(*) FROM apps").fetchone()[0] == 7


def test_apps_are_the_top_hit_by_name_and_have_their_own_pill(conn, applications):
    embedder = FakeEmbedder()
    stored(conn, embedder)
    resp = search(conn, embedder, "kalkul", log=False)
    assert resp.apps[0].name == "Kalkulator" and resp.apps_first and resp.sections()[0][0] == "apps"
    assert resp.categories[0] == "apps"
    only = search(conn, embedder, "kalkul", log=False, category="apps")
    assert only.category == "apps" and only.results == [] and only.apps[0].path.endswith("Calculator.app")
    assert app_search(conn, "def add_two(x, y):", embedder) == []


def test_opening_an_app_from_history(conn, applications):
    from lantern.search.hybrid import last_event

    embedder = FakeEmbedder()
    stored(conn, embedder)
    search(conn, embedder, "discord", log=True)
    import json

    first = json.loads(last_event(conn)["results_json"])[0]
    assert first["kind"] == "app" and first["path"].endswith("Discord.app") and first["chunk_id"] == 0


def test_completion_offers_app_names(conn, applications):
    from lantern.search.suggest import completion

    stored(conn, FakeEmbedder())
    assert completion(conn, "calcu") == "calculator"  # typed text kept, the rest of the app's name


def test_the_category_finds_apps_beside_the_one_named_like_it(conn, applications, monkeypatch):
    music = {"LSApplicationCategoryType": "public.app-category.music"}
    make_app(applications, "Music", {"CFBundleName": "Music", **music}, loctable={"pl": {"CFBundleName": "Muzyka"}})
    make_app(applications, "Spotify", {"CFBundleName": "Spotify", **music})
    local = lambda app: "Muzyka" if app.stem in ("Music", "Spotify") else None  # noqa: E731
    monkeypatch.setattr(apps_mod, "spotlight_category", local)
    stored(conn, FakeEmbedder())
    for query in ("muzyka", "music"):
        names = [h.name for h in app_search(conn, query)]
        assert names[0] == "Muzyka" and "Spotify" in names, query
    assert "Discord" not in [h.name for h in app_search(conn, "muzyka")]


def test_one_letter_lists_apps_starting_with_it(conn, applications):
    stored(conn, FakeEmbedder())
    hits = app_search(conn, "d")
    assert hits and hits[0].name == "Discord" and hits[0].match == "letter"
    assert name_match("d", ["Discord"], []) is None  # only the app search takes one letter (not settings panes)


def test_one_letter_answers_with_apps_only_and_used_ones_first(conn, applications, monkeypatch):
    from lantern import apps as apps_mod

    stored(conn, FakeEmbedder())
    make_app(applications, "Dictionary", {"CFBundleName": "Dictionary"})
    stored(conn, FakeEmbedder())
    used = next(r[0] for r in conn.execute("SELECT path FROM apps WHERE name = 'Discord'"))
    monkeypatch.setattr(apps_mod, "_usage", {used: 0.5})
    resp = search(conn, FakeEmbedder(), "d", log=False)
    assert resp.apps[0].name == "Discord"  # used, although "Dictionary" is just as good a match
    assert resp.results == [] and resp.photos == [] and resp.settings == [] and resp.sections()[0][0] == "apps"


def test_the_quick_phase_answers_by_name_without_files(conn, applications):
    stored(conn, FakeEmbedder())
    resp = search(conn, FakeEmbedder(), "kalkul", log=False, quick=True)
    assert resp.apps[0].name == "Kalkulator" and resp.results == [] and resp.photos == []
    assert resp.sections()[0][0] == "apps"

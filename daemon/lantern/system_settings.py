"""System Settings as search results, like Spotlight's: panes ("Dźwięk") and single settings ("Używaj formatu
24-godzinnego na pasku menu"), read from macOS itself, read-only.

Each pane is a Settings extension (/System/Library/ExtensionKit/Extensions/*.appex, extension point
com.apple.Settings.extension.ui). Its InfoPlist.loctable has the pane name in every language, its Info.plist the SF
Symbol and colour of its icon, and the file named by `searchTermsFileName` (<lang>.lproj/<name>.searchTerms) lists
the settings inside with a title and index words: the same data System Settings and Spotlight search. A result
opens its pane with the x-apple.systempreferences URL scheme (allowed per extension by
allowsXAppleSystemPreferencesURLScheme).
"""

import json
import os
import plistlib
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from lantern.apps import _matches, _plist

EXTENSIONS_DIR = Path("/System/Library/ExtensionKit/Extensions")
SETTINGS_POINT = "com.apple.Settings.extension.ui"


@dataclass
class SettingItem:
    key: str  # bundle id, or bundle id + "/" + setting key
    pane: str  # pane name in the first preferred language
    title: str  # the pane name for a pane; the setting's title otherwise
    url: str
    names: list[str] = field(default_factory=list)  # other names: pane / title in the other preferred languages
    keywords: list[str] = field(default_factory=list)
    symbol: str | None = None  # SF Symbol of the pane icon
    color: str | None = None  # its enclosure colour ("Blue")

    @property
    def is_pane(self) -> bool:
        return "/" not in self.key

    def document(self) -> str:
        head = self.title if self.is_pane else f"{self.title} ({self.pane})"
        return ". ".join(dict.fromkeys([head, "System Settings", *self.names, *self.keywords]))


def _pane_name(table: dict, language: str) -> str | None:
    for lang, entry in table.items():
        if isinstance(entry, dict) and _matches(lang, [language]):
            for key in ("CFBundleDisplayName", "NSPrefPaneIconLabel"):
                value = entry.get(key)
                if isinstance(value, str) and value.strip() and not value.strip().endswith("Extension"):
                    return value.replace(" ", " ").strip()
    return None


def _sidebar_name(resources: Path, settings_attrs: dict, language: str) -> str | None:
    """Panes with several "personalities" (Battery / Energy Saver) name themselves through a string key in
    `representations`, translated in one of their .loctable files."""
    keys = [r.get("sidebar-name") for r in settings_attrs.get("representations") or [] if isinstance(r, dict)]
    keys = [k for k in keys if isinstance(k, str)]
    if not keys:
        return None
    for table_path in sorted(resources.glob("*.loctable")):
        table = _plist(table_path)
        for lang, entries in table.items():
            if isinstance(entries, dict) and _matches(lang, [language]):
                for key in reversed(keys):  # the battery variant last in the list: prefer it on laptops
                    if isinstance(entries.get(key), str):
                        return entries[key].replace("\u00a0", " ").strip()
    return None


def _search_terms(resources: Path, filename: str, language: str) -> dict:
    try:
        folders = [p for p in resources.iterdir() if p.suffix == ".lproj" and _matches(p.stem, [language])]
    except OSError:
        return {}
    for folder in sorted(folders, key=lambda p: len(p.stem)):  # "pl" before "pl_PL", "en" before "en_GB"
        terms = folder / f"{filename}.searchTerms"
        if terms.is_file():
            data = _plist(terms)
            return data if isinstance(data, dict) else {}
    return {}


def _words(index: str) -> list[str]:
    return [w.replace("\u00a0", " ").strip() for w in index.split(",") if w.strip()]


def read_pane(appex: Path, languages: list[str]) -> list[SettingItem]:
    try:
        with open(appex / "Contents" / "Info.plist", "rb") as f:
            info = plistlib.load(f)
    except Exception:  # noqa: BLE001 - system files of another version: skip what does not parse
        return []
    attrs = info.get("EXAppExtensionAttributes") or {}
    settings_attrs = attrs.get("SettingsExtensionAttributes") or {}
    if attrs.get("EXExtensionPointIdentifier") != SETTINGS_POINT:
        return []
    if not settings_attrs.get("allowsXAppleSystemPreferencesURLScheme"):
        return []  # cannot be opened directly
    bundle_id = info.get("CFBundleIdentifier")
    resources = appex / "Contents" / "Resources"
    table = _plist(resources / "InfoPlist.loctable") if (resources / "InfoPlist.loctable").is_file() else {}
    names = [n for n in (_pane_name(table, lang) or _sidebar_name(resources, settings_attrs, lang)
                         for lang in languages) if n]  # fmt: skip
    if not names or not bundle_id or "FollowUp" in bundle_id:
        return []  # no human name (internal extensions such as follow-up handlers)
    icon = (info.get("CFBundleIcons") or {}).get("ISGraphicIconConfiguration") or {}
    url = f"x-apple.systempreferences:{bundle_id}"
    pane = SettingItem(key=bundle_id, pane=names[0], title=names[0], url=url, names=list(dict.fromkeys(names[1:])),
                       symbol=icon.get("ISSymbolName"), color=icon.get("ISEnclosureColor"))  # fmt: skip
    items = [pane]
    terms_file = settings_attrs.get("searchTermsFileName")
    if terms_file:
        per_language = [_search_terms(resources, terms_file, lang) for lang in languages]
        for key in dict.fromkeys(k for terms in per_language for k in terms):
            titles, keywords = [], []
            for terms in per_language:
                for entry in (terms.get(key) or {}).get("localizableStrings") or []:
                    if entry.get("title"):
                        titles.append(entry["title"].replace(" ", " ").strip())
                    keywords += _words(entry.get("index") or "")
            titles = list(dict.fromkeys(titles))
            if titles:
                items.append(SettingItem(
                    key=f"{bundle_id}/{key}", pane=pane.title, title=titles[0], url=url, names=titles[1:],
                    keywords=list(dict.fromkeys(keywords)), symbol=pane.symbol, color=pane.color,
                ))  # fmt: skip
    return items


def extensions_dir() -> Path | None:
    """LANTERN_SETTINGS_DIR replaces the system folder (tests); empty means none."""
    override = os.environ.get("LANTERN_SETTINGS_DIR")
    if override is not None:
        return Path(override) if override else None
    return EXTENSIONS_DIR


def scan_settings(languages: list[str], folder: Path | None = None) -> list[SettingItem]:
    folder = folder or extensions_dir()
    if folder is None:
        return []
    items = []
    try:
        extensions = sorted(folder.glob("*.appex"))
    except OSError:
        return []
    for appex in extensions:
        items += read_pane(appex, languages)
    return items


def signature() -> float | None:
    folder = extensions_dir()
    if folder is None:
        return None
    try:
        return folder.stat().st_mtime  # changes with system updates
    except OSError:
        return None


def store_settings(conn: sqlite3.Connection, items: list[SettingItem], languages: list[str], embed,
                   model_id: int) -> int:  # fmt: skip
    """Replace the table; vectors only for new or changed documents (or another model). Returns the number embedded."""
    lang_key = ",".join(languages)
    old = {r["key"]: r for r in conn.execute("SELECT key, document, model_id, embedding FROM system_settings")}
    todo = [i for i in items if (o := old.get(i.key)) is None or o["model_id"] != model_id
            or o["document"] != i.document()]  # fmt: skip
    vectors = embed([i.document() for i in todo]) if todo else []
    fresh = {i.key: np.asarray(v, dtype=np.float32).tobytes() for i, v in zip(todo, vectors, strict=True)}
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM system_settings")
        conn.executemany(
            "INSERT INTO system_settings(key, pane, title, names, keywords, url, symbol, color, languages, document, "
            "model_id, embedding) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (i.key, i.pane, i.title, json.dumps(i.names, ensure_ascii=False),
                 json.dumps(i.keywords, ensure_ascii=False), i.url, i.symbol, i.color, lang_key, i.document(),
                 model_id, fresh.get(i.key) or old[i.key]["embedding"])
                for i in items
            ],
        )  # fmt: skip
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return len(todo)

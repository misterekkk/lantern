"""Applications as search results, like Spotlight's: read from the bundles, never launched or changed.

For each app in the standard folders: the name Finder shows (the bundle's file name), the bundle's own names, the
names in the user's preferred languages (Apple apps keep them in InfoPlist.loctable, others in <lang>.lproj/
InfoPlist.strings), Apple's search keywords (MDItemKeywords, e.g. "Preferencje systemowe" for System Settings) and
the App Store category when the developer set one. One text vector per app (EmbeddingGemma 2, same model as the
text index) lets a description ("komunikator") find an app; names are matched as text (search/apps.py).
"""

import ctypes
import json
import math
import os
import plistlib
import sqlite3
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

APP_DIRS = (
    Path("/Applications"),
    Path("/Applications/Utilities"),
    Path("/System/Applications"),
    Path("/System/Applications/Utilities"),
    Path.home() / "Applications",
)
EXTRA_APPS = (Path("/System/Library/CoreServices/Finder.app"),)

# App Store categories (LSApplicationCategoryType), in English: the text the vector is made of. The app shows its
# own localized names for them.
CATEGORY_NAMES = {
    "business": "business", "developer-tools": "developer tools, programming, code editor",
    "education": "education", "entertainment": "entertainment", "finance": "finance, money",
    "games": "games", "graphics-design": "graphics and design, image editing",
    "healthcare-fitness": "health and fitness",
    "lifestyle": "lifestyle", "medical": "medical", "music": "music, audio player", "news": "news",
    "photography": "photography, photo editing", "productivity": "productivity, office, documents",
    "reference": "reference, dictionary", "social-networking": "social networking, chat, messaging",
    "sports": "sports", "travel": "travel, maps", "utilities": "utilities, system tools", "video": "video editing",
    "weather": "weather",
}  # fmt: skip


@dataclass
class AppInfo:
    path: str
    name: str  # as Finder shows it: in the first preferred language when the app has that translation
    names: list[str] = field(default_factory=list)  # every other name: bundle names, translations
    keywords: list[str] = field(default_factory=list)
    category: str | None = None  # "social-networking" (without the public.app-category. prefix)
    bundle_id: str | None = None
    mtime: float = 0.0

    def document(self) -> str:
        """The text the app's vector is made of."""
        parts = [self.name, *self.names]
        if self.category:
            parts.append(f"{CATEGORY_NAMES.get(self.category, self.category.replace('-', ' '))} app")
        else:
            parts.append("application")
        parts += self.keywords
        return ". ".join(dict.fromkeys(p for p in parts if p))


def app_dirs() -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """(folders, single apps). LANTERN_APP_DIRS (colon-separated) replaces both: tests and custom setups."""
    override = os.environ.get("LANTERN_APP_DIRS")
    if override is not None:
        return tuple(Path(p) for p in override.split(":") if p), ()
    return APP_DIRS, EXTRA_APPS


def iter_app_bundles() -> Iterator[Path]:
    """Apps in the standard folders, one level of subfolders deep (e.g. /Applications/Microsoft Office/…)."""
    seen = set()
    folders, extra = app_dirs()
    for folder in folders:
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            candidates = [entry] if entry.suffix == ".app" else []
            if not candidates and entry.is_dir() and not entry.is_symlink() and folder.name != "Utilities":
                try:
                    candidates = sorted(p for p in entry.iterdir() if p.suffix == ".app")
                except OSError:
                    candidates = []
            for app in candidates:
                key = app.resolve()
                if key not in seen and (app / "Contents" / "Info.plist").is_file():
                    seen.add(key)
                    yield app
    for app in extra:
        if app.resolve() not in seen and (app / "Contents" / "Info.plist").is_file():
            yield app


def _plist(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return plistlib.load(f)
    except OSError:
        return {}
    except Exception:  # noqa: BLE001 - other apps' files: malformed XML must not stop the scan
        pass
    try:  # old-style .strings files (UTF-16, "key" = "value";): plutil reads them
        out = subprocess.run(["plutil", "-convert", "json", "-o", "-", str(path)], capture_output=True, timeout=5)
        return json.loads(out.stdout) if out.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def _matches(key: str, languages: list[str]) -> bool:
    base = key.replace("_", "-").split("-")[0].lower()
    return any(base == lang.split("-")[0].lower() for lang in languages)


def _localized(resources: Path, languages: list[str]) -> list[tuple[str, dict]]:
    """(language, InfoPlist entries) for the preferred languages: from InfoPlist.loctable (one file, all languages)
    or from the <lang>.lproj folders."""
    found = []
    table = resources / "InfoPlist.loctable"
    if table.is_file():
        data = _plist(table)
        found += [(k, v) for k, v in data.items() if isinstance(v, dict) and _matches(k, languages)]
    if not found:
        try:
            lprojs = [p for p in resources.iterdir() if p.suffix == ".lproj" and _matches(p.stem, languages)]
        except OSError:
            lprojs = []
        for lproj in lprojs:
            strings = lproj / "InfoPlist.strings"
            if strings.is_file():
                found.append((lproj.stem, _plist(strings)))
    return found


def _keywords(value) -> list[str]:
    if isinstance(value, str):
        return [k.strip() for k in value.split(",") if k.strip()]
    if isinstance(value, list):
        return [str(k).strip() for k in value if str(k).strip()]
    return []


def _spotlight(app: Path, names: list[str]) -> dict:
    """Spotlight metadata of a bundle (MDItem through ctypes): strings, numbers and dates (as Unix time)."""
    from lantern.dictionary import _UTF8, _frameworks

    if (fw := _frameworks()) is None:
        return {}
    cs, cf = fw
    cs.MDItemCreate.restype, cs.MDItemCreate.argtypes = ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p]
    cs.MDItemCopyAttribute.restype = ctypes.c_void_p
    cs.MDItemCopyAttribute.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    cf.CFGetTypeID.restype, cf.CFGetTypeID.argtypes = ctypes.c_ulong, [ctypes.c_void_p]
    for fn in ("CFStringGetTypeID", "CFNumberGetTypeID", "CFDateGetTypeID"):
        getattr(cf, fn).restype = ctypes.c_ulong
    cf.CFNumberGetValue.restype = ctypes.c_bool
    cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    cf.CFDateGetAbsoluteTime.restype, cf.CFDateGetAbsoluteTime.argtypes = ctypes.c_double, [ctypes.c_void_p]
    out: dict = {}
    path = cf.CFStringCreateWithCString(None, str(app).encode(), _UTF8)
    item = cs.MDItemCreate(None, path) if path else None
    try:
        for name in names if item else []:
            key = cf.CFStringCreateWithCString(None, name.encode(), _UTF8)
            value = cs.MDItemCopyAttribute(item, key) if key else None
            try:
                if not value:
                    continue
                kind = cf.CFGetTypeID(value)
                if kind == cf.CFStringGetTypeID():
                    buf = ctypes.create_string_buffer(cf.CFStringGetLength(value) * 4 + 1)
                    if cf.CFStringGetCString(value, buf, len(buf), _UTF8):
                        out[name] = buf.value.decode().strip()
                elif kind == cf.CFNumberGetTypeID():
                    number = ctypes.c_int64()
                    if cf.CFNumberGetValue(value, 4, ctypes.byref(number)):  # kCFNumberSInt64Type
                        out[name] = number.value
                elif kind == cf.CFDateGetTypeID():
                    out[name] = cf.CFDateGetAbsoluteTime(value) + 978307200  # 2001-01-01 -> Unix time
            finally:
                for ref in (value, key):
                    if ref:
                        cf.CFRelease(ref)
    finally:
        for ref in (item, path):
            if ref:
                cf.CFRelease(ref)
    return out


def spotlight_category(app: Path) -> str | None:
    """The App Store category as Spotlight names it, in the system language ("Muzyka")."""
    return _spotlight(app, ["kMDItemAppStoreCategory"]).get("kMDItemAppStoreCategory") or None


# How much each app is used, as macOS counts it (kMDItemUseCount / kMDItemLastUsedDate, the data behind Spotlight's
# own ranking): ties between name matches go to the app actually used, from the first day ("d" -> Discord, not the
# shortest name starting with d). Read every few minutes in the control loop; kept in memory only.
_usage: dict[str, float] = {}


def refresh_usage(paths: list[str]) -> None:
    now = time.time()
    fresh = {}
    for path in paths:
        meta = _spotlight(Path(path), ["kMDItemUseCount", "kMDItemLastUsedDate"])
        count, last = meta.get("kMDItemUseCount") or 0, meta.get("kMDItemLastUsedDate")
        if count and last:
            days = max(0.0, (now - last) / 86400)
            fresh[path] = min(1.0, math.log10(1 + count) / 4) * 0.5 ** (days / 30)  # 30-day half-life
    _usage.clear()
    _usage.update(fresh)


def usage(path: str) -> float:
    """0..1: how much an app is used now (log of launches, halved every 30 days without use)."""
    return _usage.get(path, 0.0)


def read_app(app: Path, languages: list[str]) -> AppInfo | None:
    info = _plist(app / "Contents" / "Info.plist")
    if not info or info.get("LSBackgroundOnly") in (True, "1"):
        return None  # background helpers are not apps one opens (menu bar apps are: Spotlight lists them too)
    names = [info.get("CFBundleDisplayName"), info.get("CFBundleName")]
    keywords = _keywords(info.get("MDItemKeywords"))
    display = None  # the name in the first preferred language, as Finder and Spotlight show it ("Kalkulator")
    for lang, entry in _localized(app / "Contents" / "Resources", languages):
        local = entry.get("CFBundleDisplayName") or entry.get("CFBundleName")
        if display is None and isinstance(local, str) and local.strip() and _matches(lang, languages[:1]):
            display = local.strip()
        names += [entry.get("CFBundleDisplayName"), entry.get("CFBundleName")]
        keywords += _keywords(entry.get("MDItemKeywords"))
    category = info.get("LSApplicationCategoryType")
    if isinstance(category, str) and category.startswith("public.app-category."):
        category = category.removeprefix("public.app-category.")
    else:
        category = None
    if category and (local := spotlight_category(app)):
        keywords.append(local)  # "muzyka" finds Spotify too, not only the app named Muzyka
    name = display or app.stem
    clean = [n.strip() for n in [app.stem, *names] if isinstance(n, str) and n.strip() and n.strip() != name]
    return AppInfo(
        path=str(app), name=name, names=list(dict.fromkeys(clean)), keywords=list(dict.fromkeys(keywords)),
        category=category, bundle_id=info.get("CFBundleIdentifier"), mtime=os.stat(app).st_mtime,
    )  # fmt: skip


def scan_apps(languages: list[str]) -> list[AppInfo]:
    apps = []
    for bundle in iter_app_bundles():
        try:
            app = read_app(bundle, languages)
        except OSError:
            app = None
        if app is not None:
            apps.append(app)
    return apps


def folders_signature() -> tuple:
    """Changes when an app is added, removed or updated in one of the folders (cheap: folder mtimes)."""
    sig = []
    for folder in app_dirs()[0]:
        try:
            sig.append((str(folder), folder.stat().st_mtime))
        except OSError:
            sig.append((str(folder), None))
    return tuple(sig)


def store_apps(conn: sqlite3.Connection, apps: list[AppInfo], languages: list[str], embed, model_id: int) -> int:
    """Replace the apps table; vectors are recomputed only for new or changed apps (or another model / languages).
    `embed(list[str]) -> np.ndarray` takes document texts (already formatted). Returns the number embedded."""
    lang_key = ",".join(languages)
    old = {r["path"]: r for r in conn.execute("SELECT path, mtime, model_id, languages, document, embedding FROM apps")}
    todo = [
        a for a in apps
        if (o := old.get(a.path)) is None or o["model_id"] != model_id or o["languages"] != lang_key
        or o["mtime"] != a.mtime or o["document"] != a.document()
    ]  # fmt: skip
    vectors = embed([a.document() for a in todo]) if todo else []
    fresh = {a.path: np.asarray(v, dtype=np.float32).tobytes() for a, v in zip(todo, vectors, strict=True)}
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM apps")
        conn.executemany(
            "INSERT INTO apps(path, name, names, keywords, category, bundle_id, mtime, languages, document, model_id, "
            "embedding) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (a.path, a.name, json.dumps(a.names, ensure_ascii=False), json.dumps(a.keywords, ensure_ascii=False),
                 a.category, a.bundle_id, a.mtime, lang_key, a.document(), model_id,
                 fresh.get(a.path) or old[a.path]["embedding"])
                for a in apps
            ],
        )  # fmt: skip
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return len(todo)


@dataclass(frozen=True)
class LauncherCommand:
    """A command of another launcher, shown with the user's shortcuts: a Raycast extension command (opened through
    its raycast:// link) or a keyword of an Alfred workflow (opened in Alfred with the keyword typed)."""

    id: str
    name: str
    keyword: str | None
    source: str  # raycast | alfred
    detail: str  # the extension or workflow it belongs to
    url: str  # raycast://extensions/… or alfred:search:<keyword> (the app runs Alfred's "search" AppleScript)


RAYCAST_EXTENSIONS = Path.home() / ".config" / "raycast" / "extensions"
ALFRED_HOME = Path.home() / "Library" / "Application Support" / "Alfred"
_launcher: list[LauncherCommand] = []


def scan_raycast(root: Path = RAYCAST_EXTENSIONS) -> list[LauncherCommand]:
    """Commands of installed Raycast extensions, from each extension's package.json (names only). Raycast's own
    built-in commands are not on disk in a readable form, so they are not listed; Lantern's extension is skipped."""
    found = []
    for manifest in sorted(root.glob("*/package.json")) if root.is_dir() else []:
        try:
            ext = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name, owner = ext.get("name"), ext.get("owner") or ext.get("author")
        if not isinstance(name, str) or not isinstance(owner, str) or name == "lantern":
            continue
        for cmd in ext.get("commands") or []:
            if not isinstance(cmd, dict) or cmd.get("mode") == "menu-bar" or not cmd.get("name"):
                continue
            title = str(cmd.get("title") or cmd["name"])
            url = f"raycast://extensions/{owner}/{name}/{cmd['name']}"
            found.append(LauncherCommand(url, title, None, "raycast", str(ext.get("title") or name), url))
    return found


def _alfred_preferences(home: Path = ALFRED_HOME) -> Path:
    """Alfred.alfredpreferences, also when synced elsewhere (prefs.json names the current one)."""
    try:
        current = json.loads((home / "prefs.json").read_text(encoding="utf-8")).get("current")
        if isinstance(current, str) and Path(current).is_dir():
            return Path(current)
    except (OSError, ValueError):
        pass
    return home / "Alfred.alfredpreferences"


def scan_alfred(home: Path = ALFRED_HOME) -> list[LauncherCommand]:
    """Keywords of the user's enabled Alfred workflows (Keyword and Script Filter inputs), from each workflow's
    info.plist: the keyword, its title and the workflow's name; nothing else is read."""
    found = []
    for info in sorted((_alfred_preferences(home) / "workflows").glob("*/info.plist")):
        try:
            with info.open("rb") as f:
                wf = plistlib.load(f)
        except (OSError, plistlib.InvalidFileException, ValueError):
            continue
        if wf.get("disabled") or wf.get("bundleid") == "com.lantern.alfred":
            continue
        for obj in wf.get("objects") or []:
            if obj.get("type") not in ("alfred.workflow.input.keyword", "alfred.workflow.input.scriptfilter"):
                continue
            config = obj.get("config") or {}
            keyword = str(config.get("keyword") or "").strip()
            if not keyword or obj.get("disabled"):
                continue
            title = str(config.get("title") or config.get("text") or wf.get("name") or keyword)
            ident = f"alfred:{wf.get('bundleid') or info.parent.name}:{keyword}"
            found.append(LauncherCommand(ident, title, keyword, "alfred", str(wf.get("name") or ""),
                                         f"alfred:search:{keyword}"))  # fmt: skip
    return found


def refresh_launcher_commands() -> None:
    """Kept in memory (no schema change): re-read with the apps, a few milliseconds."""
    global _launcher
    _launcher = scan_raycast() + scan_alfred()


def launcher_commands() -> list[LauncherCommand]:
    return _launcher


def scan_shortcuts() -> list[tuple[str, str]]:
    """(identifier, name) of the user's shortcuts, from the Shortcuts command line tool (local)."""
    try:
        out = subprocess.run(["shortcuts", "list", "--show-identifiers"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.endswith(")") and " (" in line:
            name, ident = line[:-1].rsplit(" (", 1)
            found.append((ident, name.replace("\u00a0", " ")))
    return found


def store_shortcuts(conn: sqlite3.Connection, shortcuts: list[tuple[str, str]]) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM shortcuts")
        conn.executemany("INSERT OR REPLACE INTO shortcuts(id, name) VALUES (?, ?)", shortcuts)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

"""Folder tree for the settings window: one folder level at a time, each entry with its indexing state.

States: included | partial (a folder with something excluded inside) | excluded (a user exclusion, can be toggled)
| skipped (fixed rules, file type settings, .gitignore, patterns, iCloud: shown with a reason, not toggleable here).
Toggling writes ordinary path exclusions to the settings (`exclude`), so the scanner needs nothing new.
Read-only towards the user's files: lists folders and stats entries, never reads file contents.
"""

import os
import sqlite3
import time
import unicodedata
from pathlib import Path

import pathspec

from lantern.scanner.walk import (
    Filters,
    UserExcludes,
    _expand,
    _load_gitignore,
    dir_skip_reason,
    file_skip_reason,
    is_dataless,
    normalize_extension,
)
from lantern.service.settings import Settings
from lantern.textnorm import fold

MAX_ENTRIES = 5000  # per folder level


def _filters(settings: Settings) -> Filters:
    exts = frozenset(e for e in (normalize_extension(x) for x in settings.exclude_extensions) if e)
    return Filters(skip_extensions=exts, include_images=settings.index_photos, include_media=settings.index_audio_video)


def _excluded_paths(settings: Settings) -> list[tuple[str, Path]]:
    """User path exclusions as (original string, resolved path); patterns (`*.log`) are not paths."""
    return [(e, _expand(e).resolve()) for e in settings.exclude if e.startswith(("/", "~"))]


def root_of(path: Path, roots: list[str]) -> Path | None:
    best = None
    for r in roots:
        rp = Path(r)
        if (path == rp or rp in path.parents) and (best is None or len(str(rp)) > len(str(best))):
            best = rp
    return best


def _gitignore_chain(root: Path, folder: Path) -> list[tuple[Path, pathspec.GitIgnoreSpec]]:
    """.gitignore files from the indexed root down to `folder` (inherited like in the scanner)."""
    parts = folder.relative_to(root).parts
    chain = []
    for d in [root.joinpath(*parts[:i]) for i in range(len(parts) + 1)]:
        spec = _load_gitignore(d)
        if spec is not None:
            chain.append((d, spec))
    return chain


def _gitignored(chain, path: Path, is_dir: bool) -> bool:
    for spec_dir, spec in chain:
        if spec_dir in path.parents and spec.match_file(
            path.relative_to(spec_dir).as_posix() + ("/" if is_dir else "")
        ):
            return True
    return False


def _indexed_under(conn: sqlite3.Connection, folder: str) -> int:
    # Range on the UNIQUE(path) index: "/a/b/" <= path < "/a/b0" ("0" sorts right after "/").
    return conn.execute(
        "SELECT COUNT(*) FROM files WHERE path >= ? AND path < ?", (folder + "/", folder + "0")
    ).fetchone()[0]


class _Classifier:
    """State and reason of tree entries under one indexed root (shared by folder listing and search)."""

    def __init__(self, conn: sqlite3.Connection, root: Path, settings: Settings):
        self.conn, self.root, self.home, self.filters = conn, root, Path.home(), _filters(settings)
        self.patterns = UserExcludes([e for e in settings.exclude if not e.startswith(("/", "~"))])
        self.excluded = [p for _, p in _excluded_paths(settings)]
        self._chains: dict[Path, list] = {}

    def chain(self, folder: Path) -> list:
        if folder not in self._chains:
            self._chains[folder] = _gitignore_chain(self.root, folder)
        return self._chains[folder]

    def entry(self, entry: os.DirEntry, indexed_file: bool | None = None) -> dict:
        child = Path(entry.path)
        is_dir = entry.is_dir(follow_symlinks=False)
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            st = None
        if entry.is_symlink():
            reason = "symlink"
        elif is_dir:
            reason = dir_skip_reason(entry.name, child, self.home)
        else:
            reason = file_skip_reason(entry.name, child, self.filters)
        if reason is None and _gitignored(self.chain(child.parent), child, is_dir):
            reason = "gitignore"
        if reason is None and self.patterns.spec is not None and self.patterns.match(child, self.root, is_dir):
            reason = "pattern"
        if reason is None and st is not None and is_dataless(st):
            reason = "cloud"
        if reason is not None:
            state = "skipped"
        elif any(child == p or p in child.parents for p in self.excluded):
            state = "excluded"
        elif is_dir and any(child in p.parents for p in self.excluded):
            state = "partial"
        else:
            state = "included"
        if is_dir:
            indexed = _indexed_under(self.conn, str(child))
        elif indexed_file is not None:
            indexed = int(indexed_file)
        else:
            indexed = self.conn.execute("SELECT COUNT(*) FROM files WHERE path = ?", (str(child),)).fetchone()[0]
        return {"name": entry.name, "path": str(child), "is_dir": is_dir, "state": state, "reason": reason,
                "indexed": indexed}  # fmt: skip


def list_children(conn: sqlite3.Connection, folder: str, roots: list[str], settings: Settings) -> dict:
    path = Path(folder).expanduser().resolve()
    root = root_of(path, roots)
    if root is None:
        raise ValueError(f"{path} is not inside an indexed folder")
    try:
        if is_dataless(path.lstat()):  # listing would download it from iCloud
            return {"path": str(path), "entries": [], "note": "cloud"}
        with os.scandir(path) as it:
            raw = sorted(it, key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
    except PermissionError:
        return {"path": str(path), "entries": [], "note": "no_access"}
    truncated = len(raw) > MAX_ENTRIES
    raw = raw[:MAX_ENTRIES]
    file_paths = [e.path for e in raw if not e.is_dir(follow_symlinks=False)]
    indexed_files: set[str] = set()
    for i in range(0, len(file_paths), 500):
        batch = file_paths[i : i + 500]
        marks = ",".join("?" * len(batch))
        indexed_files |= {r[0] for r in conn.execute(f"SELECT path FROM files WHERE path IN ({marks})", batch)}
    classify = _Classifier(conn, root, settings)
    entries = [classify.entry(e, e.path in indexed_files) for e in raw]
    return {"path": str(path), "entries": entries, "truncated": truncated}


def _fold(text: str) -> str:
    """Case- and accent-insensitive form ("Zażółć" -> "zazolc"); ł has no decomposition, textnorm.fold maps it."""
    return "".join(c for c in unicodedata.normalize("NFKD", fold(text).casefold()) if not unicodedata.combining(c))


def search(
    conn: sqlite3.Connection, query: str, roots: list[str], settings: Settings, limit: int = 100, budget_s: float = 3.0
) -> dict:
    """Files and folders whose name contains `query` (case and accents ignored), in the indexed folders.

    Walks the folders (names only, read-only); never descends into folders that are always skipped (build/cache,
    hidden, packages, iCloud-only) but does descend into excluded ones, so excluded items can be found and checked
    again. Stops at `limit` matches or after `budget_s` seconds (`truncated`).
    """
    needle = _fold(query.strip())
    if not needle:
        return {"query": query, "entries": [], "truncated": False}
    deadline = time.monotonic() + budget_s
    found, truncated = [], False
    for root_str in roots:
        root = Path(root_str)
        if not root.is_dir():
            continue
        classify = _Classifier(conn, root, settings)
        stack = [root]
        while stack and not truncated:
            folder = stack.pop()
            try:
                if folder != root and is_dataless(folder.lstat()):
                    continue
                with os.scandir(folder) as it:
                    entries = list(it)
            except OSError:
                continue
            for e in entries:
                is_dir = e.is_dir(follow_symlinks=False)
                if needle in _fold(e.name):
                    found.append(classify.entry(e))
                if is_dir and not e.is_symlink() and dir_skip_reason(e.name, Path(e.path), classify.home) is None:
                    stack.append(Path(e.path))
                if len(found) >= limit or time.monotonic() > deadline:
                    truncated = True
                    break
    found.sort(key=lambda x: (not x["is_dir"], len(x["path"]), x["path"].lower()))
    return {"query": query, "entries": found, "truncated": truncated}


def set_included(settings: Settings, target: str, included: bool) -> list[str]:
    """Check or uncheck a file/folder by editing path exclusions. Returns the new exclusion list.

    Unchecking adds the path (nested exclusions become redundant and are dropped). Checking removes the path and
    anything excluded inside it; if an ancestor folder is excluded, that exclusion is replaced by exclusions of the
    siblings along the way, so only the checked item comes back.
    """
    path = Path(target).expanduser().resolve()
    paths = _excluded_paths(settings)
    keep = [e for e, p in paths if not (p == path or path in p.parents)]  # drop the path itself and nested ones
    patterns = [e for e in settings.exclude if not e.startswith(("/", "~"))]
    if not included:
        settings.exclude = patterns + keep + [str(path)]
        return settings.exclude
    ancestor = next(((e, p) for e, p in paths if p in path.parents), None)
    if ancestor is not None:
        keep.remove(ancestor[0])
        home, filters = Path.home(), Filters()
        node = path
        while node != ancestor[1]:
            parent = node.parent
            try:
                siblings = [s for s in os.scandir(parent) if s.name != node.name and not s.is_symlink()]
            except OSError:
                siblings = []
            for s in siblings:
                sp = parent / s.name
                rule = dir_skip_reason(s.name, sp, home) if s.is_dir(follow_symlinks=False) else (
                    file_skip_reason(s.name, sp, filters))  # fmt: skip
                if rule is None or rule in {"extension", "photos_off", "media_off"}:
                    keep.append(str(sp))
            node = parent
    settings.exclude = patterns + keep
    return settings.exclude


def removal_preview(conn: sqlite3.Connection, target: str) -> int:
    """How many indexed files unchecking this file/folder would remove from the index."""
    path = str(Path(target).expanduser().resolve())
    exact = conn.execute("SELECT COUNT(*) FROM files WHERE path = ?", (path,)).fetchone()[0]
    return exact + _indexed_under(conn, path)

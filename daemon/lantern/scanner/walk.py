"""Filesystem walk: default exclusions, secrets, nested .gitignore, macOS bundles, iCloud placeholders and the
home-folder policy.

- Hidden directories and files are skipped (dotfiles, ~/.ssh, ~/.aws, caches), except `.github`.
- macOS bundles/packages (`*.app`, `*.photoslibrary`, ...) are opaque: never descended into.
- Under the home folder, `~/Library` is skipped (mail, caches, app data), except iCloud Drive
  (`~/Library/Mobile Documents/com~apple~CloudDocs`); also skipped: `~/Applications`, the Apple Music media folder.
- iCloud files that exist only in the cloud (`SF_DATALESS`) are skipped: reading them would download them. They are
  indexed once they are on the disk.
- Directories that cannot be read (macOS privacy permissions, chmod) are reported, so that their previously indexed
  files are not mistaken for deleted ones.
Symlinks are never followed.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pathspec

from lantern.config import EXCLUDED_DIRS, GENERATED_FILES, SECRET_PATTERNS
from lantern.extractors import kind_for

_SECRETS = pathspec.GitIgnoreSpec.from_lines(SECRET_PATTERNS)

SF_DATALESS = 0x40000000  # <sys/stat.h>: file content not on the local disk (iCloud "optimise storage")
HIDDEN_ALLOWED = frozenset({".github"})
BUNDLE_SUFFIXES = frozenset(
    {
        ".app", ".photoslibrary", ".musiclibrary", ".tvlibrary", ".imovielibrary", ".fcpbundle", ".logicx", ".band",
        ".xcarchive", ".framework", ".bundle", ".plugin", ".kext", ".pkg", ".mpkg", ".dsym", ".xcassets",
        ".xcodeproj", ".xcworkspace", ".photolibrary", ".aplibrary", ".migratedphotolibrary", ".vmwarevm", ".utm",
        ".pvm", ".theater", ".sparsebundle", ".sparseimage", ".dmg", ".numbers", ".pages", ".key", ".rtfd", ".scriptd",
    }
)  # fmt: skip
ICLOUD_DRIVE = Path("Library") / "Mobile Documents" / "com~apple~CloudDocs"
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".heic", ".heif", ".gif", ".webp", ".tif", ".tiff", ".bmp"})
HOME_SKIP = (Path("Library"), Path("Applications"), Path("Music") / "Music", Path("Music") / "iTunes")


@dataclass(frozen=True)
class FileEntry:
    path: Path
    size: int
    mtime_ns: int
    kind: str


@dataclass
class WalkReport:
    """Side information collected while iterating (filled as the iterator runs). Files under `protected_dirs` and
    `cloud_files` exist but were not read: their index entries must be kept, not purged."""

    unreadable: list[Path] = field(default_factory=list)  # no permission (macOS privacy, chmod)
    cloud_dirs: list[Path] = field(default_factory=list)  # iCloud folders not on the disk
    cloud_files: set[str] = field(default_factory=set)  # iCloud files not on the disk
    images: int = 0  # photos seen (not indexed until Phase 7)

    @property
    def cloud_only(self) -> int:
        return len(self.cloud_files) + len(self.cloud_dirs)

    def protects(self, path: str) -> bool:
        if path in self.cloud_files:
            return True
        return any(path.startswith(str(d) + os.sep) for d in (*self.unreadable, *self.cloud_dirs))


def _expand(pattern: str) -> Path:
    """`~/x` relative to Path.home() (same notion of home as the rest of the scanner)."""
    if pattern == "~" or pattern.startswith("~/"):
        return Path.home() / pattern[2:]
    return Path(pattern)


class UserExcludes:
    """User exclusions from settings: absolute paths (~ allowed) or .gitignore-style patterns relative to the
    indexed folder."""

    def __init__(self, patterns: list[str] | tuple[str, ...] = ()):
        self.paths = [_expand(p).resolve() for p in patterns if p.startswith(("/", "~"))]
        rel = [p for p in patterns if not p.startswith(("/", "~"))]
        self.spec = pathspec.GitIgnoreSpec.from_lines(rel) if rel else None

    def match(self, path: Path, root: Path, is_dir: bool) -> bool:
        if any(path == p or p in path.parents for p in self.paths):
            return True
        if self.spec is None:
            return False
        return self.spec.match_file(path.relative_to(root).as_posix() + ("/" if is_dir else ""))


def is_secret(name: str) -> bool:
    return _SECRETS.match_file(name)


def is_dataless(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_flags", 0) & SF_DATALESS)


def _load_gitignore(directory: Path) -> pathspec.GitIgnoreSpec | None:
    gi = directory / ".gitignore"
    try:
        with open(gi, encoding="utf-8", errors="replace") as f:
            return pathspec.GitIgnoreSpec.from_lines(f)
    except OSError:
        return None


def _skip_dir(name: str, path: Path, home: Path) -> bool:
    if name in EXCLUDED_DIRS:
        return True
    if name.startswith(".") and name not in HIDDEN_ALLOWED:
        return True
    if Path(name).suffix.lower() in BUNDLE_SUFFIXES:
        return True
    try:
        rel = path.relative_to(home)
    except ValueError:
        return False
    return rel in HOME_SKIP


def iter_files(
    root: Path, report: WalkReport | None = None, excludes: list[str] | tuple[str, ...] = ()
) -> Iterator[FileEntry]:
    """Yield indexable regular files under root. Symlinks are never followed."""
    report = report if report is not None else WalkReport()
    home = Path.home()
    user = UserExcludes(excludes)
    yield from _walk(root, home, report, user)
    # The home folder includes iCloud Drive even though ~/Library is skipped.
    icloud = home / ICLOUD_DRIVE
    if root == home and icloud.is_dir():
        yield from _walk(icloud, home, report, user)


def _walk(root: Path, home: Path, report: WalkReport, user: UserExcludes) -> Iterator[FileEntry]:
    # Stack of (directory the .gitignore lives in, spec), inherited by subdirectories.
    specs: dict[Path, list[tuple[Path, pathspec.GitIgnoreSpec]]] = {root: []}

    def ignored(base_specs, path: Path, is_dir: bool) -> bool:
        for spec_dir, spec in base_specs:
            rel = path.relative_to(spec_dir).as_posix() + ("/" if is_dir else "")
            if spec.match_file(rel):
                return True
        return False

    def on_error(err: OSError) -> None:
        if err.filename:
            report.unreadable.append(Path(err.filename))

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=on_error):
        current = Path(dirpath)
        inherited_specs = specs.get(current, [])
        own = _load_gitignore(current)
        active = [*inherited_specs, (current, own)] if own else inherited_specs

        keep = []
        for d in sorted(dirnames):
            sub = current / d
            if _skip_dir(d, sub, home) or ignored(active, sub, is_dir=True) or user.match(sub, root, is_dir=True):
                continue
            try:
                st = sub.lstat()
            except OSError:
                report.unreadable.append(sub)
                continue
            if os.path.islink(sub):
                continue
            if is_dataless(st):  # a folder not yet downloaded from iCloud: listing it would fetch it
                report.cloud_dirs.append(sub)
                continue
            keep.append(d)
            specs[sub] = active
        dirnames[:] = keep
        specs.pop(current, None)

        for name in sorted(filenames):
            if name.startswith(".") or is_secret(name) or name in GENERATED_FILES:
                continue
            path = current / name
            kind = kind_for(path)
            if kind is None:
                if path.suffix.lower() in IMAGE_SUFFIXES:
                    report.images += 1
                continue
            if ignored(active, path, is_dir=False) or user.match(path, root, is_dir=False):
                continue
            try:
                st = path.lstat()
            except OSError:
                continue
            if not os.path.isfile(path) or os.path.islink(path):
                continue
            if is_dataless(st):
                report.cloud_files.add(str(path))
                continue
            yield FileEntry(path=path, size=st.st_size, mtime_ns=st.st_mtime_ns, kind=kind)

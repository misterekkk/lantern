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
from lantern.extractors import MEDIA_KINDS, kind_for

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
    images: int = 0  # photos seen while photo indexing is off

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


def normalize_extension(raw: str) -> str | None:
    """`csv`, `.csv`, `*.csv` and `CSV` all mean ".csv"; None for something that is not an extension."""
    ext = raw.strip().lower().lstrip("*")
    ext = ext if ext.startswith(".") else "." + ext
    body = ext[1:]
    if not body or len(ext) > 24 or body.startswith(".") or not all(c.isalnum() or c in "._+-" for c in body):
        return None
    return ext


@dataclass(frozen=True)
class Filters:
    """Settings-driven choices of what to index (besides the fixed rules above)."""

    skip_extensions: frozenset[str] = frozenset()  # normalised (".csv"); multi-part ones (".tar.gz") work too
    include_images: bool = True
    include_media: bool = True  # audio files and videos


def dir_skip_reason(name: str, path: Path, home: Path) -> str | None:
    """Why a folder is never descended into (fixed rules), or None."""
    if name in EXCLUDED_DIRS:
        return "build"
    if name.startswith(".") and name not in HIDDEN_ALLOWED:
        return "hidden"
    if Path(name).suffix.lower() in BUNDLE_SUFFIXES:
        return "bundle"
    try:
        rel = path.relative_to(home)
    except ValueError:
        return None
    return "system" if rel in HOME_SKIP else None


def file_skip_reason(name: str, path: Path, filters: Filters) -> str | None:
    """Why a file is not indexed (fixed rules and settings, without .gitignore/user paths), or None."""
    if name.startswith("."):
        return "hidden"
    if is_secret(name):
        return "secret"
    if name in GENERATED_FILES:
        return "generated"
    lower = name.lower()
    if any(lower.endswith(ext) for ext in filters.skip_extensions):
        return "extension"
    kind = kind_for(path)
    if kind is None:
        return "unsupported"
    if kind == "image" and not filters.include_images:
        return "photos_off"
    if kind in MEDIA_KINDS and not filters.include_media:
        return "media_off"
    return None


def iter_files(
    root: Path,
    report: WalkReport | None = None,
    excludes: list[str] | tuple[str, ...] = (),
    include_images: bool = True,
    skip_extensions: list[str] | tuple[str, ...] = (),
    include_media: bool = True,
) -> Iterator[FileEntry]:
    """Yield indexable regular files under root. Symlinks are never followed."""
    report = report if report is not None else WalkReport()
    home = Path.home()
    user = UserExcludes(excludes)
    exts = frozenset(e for e in (normalize_extension(x) for x in skip_extensions) if e)
    filters = Filters(skip_extensions=exts, include_images=include_images, include_media=include_media)
    yield from _walk(root, home, report, user, filters)
    # The home folder includes iCloud Drive even though ~/Library is skipped.
    icloud = home / ICLOUD_DRIVE
    if root == home and icloud.is_dir():
        yield from _walk(icloud, home, report, user, filters)


def _ancestor_gitignores(root: Path) -> list[tuple[Path, pathspec.GitIgnoreSpec]]:
    """.gitignore files above `root` up to the top of its git repository (indexing repo/src still honours
    repo/.gitignore). Outside a repository, nothing above the indexed folder applies."""
    if (root / ".git").exists():
        return []
    chain = []
    for directory in root.parents:
        chain.append(directory)
        if (directory / ".git").exists():
            break
    else:
        return []  # not inside a repository
    specs = [(d, spec) for d in reversed(chain) if (spec := _load_gitignore(d))]
    return specs


def _walk(root: Path, home: Path, report: WalkReport, user: UserExcludes, filters: Filters) -> Iterator[FileEntry]:
    # Stack of (directory the .gitignore lives in, spec), inherited by subdirectories.
    specs: dict[Path, list[tuple[Path, pathspec.GitIgnoreSpec]]] = {root: _ancestor_gitignores(root)}

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
            if dir_skip_reason(d, sub, home) or ignored(active, sub, is_dir=True) or user.match(sub, root, is_dir=True):
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
            path = current / name
            reason = file_skip_reason(name, path, filters)
            if reason == "photos_off":
                report.images += 1
            if reason:
                continue
            kind = kind_for(path)
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

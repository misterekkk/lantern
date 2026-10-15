"""Filesystem walk with default exclusions, secret-file filtering and nested .gitignore support."""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pathspec

from lantern.config import EXCLUDED_DIRS, GENERATED_FILES, SECRET_PATTERNS
from lantern.extractors import kind_for

_SECRETS = pathspec.GitIgnoreSpec.from_lines(SECRET_PATTERNS)


@dataclass(frozen=True)
class FileEntry:
    path: Path
    size: int
    mtime_ns: int
    kind: str


def is_secret(name: str) -> bool:
    return _SECRETS.match_file(name)


def _load_gitignore(directory: Path) -> pathspec.GitIgnoreSpec | None:
    gi = directory / ".gitignore"
    try:
        with open(gi, encoding="utf-8", errors="replace") as f:
            return pathspec.GitIgnoreSpec.from_lines(f)
    except OSError:
        return None


def iter_files(root: Path) -> Iterator[FileEntry]:
    """Yield indexable regular files under root. Symlinks are never followed."""
    # Stack of (directory the .gitignore lives in, spec), inherited by subdirectories.
    specs: dict[Path, list[tuple[Path, pathspec.GitIgnoreSpec]]] = {}

    def ignored(base_specs: list[tuple[Path, pathspec.GitIgnoreSpec]], path: Path, is_dir: bool) -> bool:
        for spec_dir, spec in base_specs:
            rel = path.relative_to(spec_dir).as_posix() + ("/" if is_dir else "")
            if spec.match_file(rel):
                return True
        return False

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        inherited = specs.get(current, [])
        own = _load_gitignore(current)
        active = [*inherited, (current, own)] if own else inherited

        keep = []
        for d in sorted(dirnames):
            sub = current / d
            if d in EXCLUDED_DIRS or sub.is_symlink() or ignored(active, sub, is_dir=True):
                continue
            keep.append(d)
            specs[sub] = active
        dirnames[:] = keep
        specs.pop(current, None)

        for name in sorted(filenames):
            path = current / name
            if is_secret(name) or name in GENERATED_FILES or ignored(active, path, is_dir=False):
                continue
            kind = kind_for(path)
            if kind is None:
                continue
            try:
                st = path.lstat()
            except OSError:
                continue
            if not os.path.isfile(path) or path.is_symlink():
                continue
            yield FileEntry(path=path, size=st.st_size, mtime_ns=st.st_mtime_ns, kind=kind)

"""Which folders may become indexed roots, and how big a folder is before it is added (phase 17 fool-proofing).

Refused: the whole disk, system folders and the Lantern data folder; indexing them reads nothing useful and fills the
index with system files. A folder that is merely big is allowed after the user saw an estimate (the app asks; the CLI
asks unless `--yes`).
"""

import os
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lantern.config import lantern_home
from lantern.extractors import MEDIA_KINDS
from lantern.scanner.walk import ICLOUD_DRIVE, WalkReport, iter_files

# Whole trees that are never user files.
SYSTEM_TREES = ("/System", "/Library", "/usr", "/bin", "/sbin", "/cores", "/dev", "/etc", "/private/etc",
                "/private/var/db", "/Applications")  # fmt: skip
# Folders refused themselves (they hold everything or other people's files) but whose subfolders are fine.
TOO_WIDE = ("/", "/Users", "/Volumes", "/private", "/private/var", "/var", "/System/Volumes/Data")
USER_LIBRARY = tuple(Path.home() / p for p in (ICLOUD_DRIVE, Path("Library") / "CloudStorage"))

# Rough indexing speed on an M-series Mac (DECISIONS.md: code ~2.4 files/s, photo pair ~3–4 photos/s); recordings
# depend on their length, which a walk cannot know, so they are counted but not timed.
SECONDS_PER_FILE = {"image": 0.3}
SECONDS_PER_TEXT_FILE = 0.4
LARGE_FILES = 20_000
LARGE_BYTES = 50 * 1024**3
ESTIMATE_BUDGET_S = 4.0


def refusal(path: Path) -> str | None:
    """Why this folder cannot be indexed ("disk" | "system" | "lantern"), or None. `path` is resolved."""
    p = str(path)
    if p in TOO_WIDE:
        return "disk"
    if any(p == t or p.startswith(t + "/") for t in SYSTEM_TREES):
        return "system"
    home = Path.home()
    library = home / "Library"
    # iCloud Drive and the cloud storage apps (Dropbox, Google Drive, OneDrive) keep user files in ~/Library
    in_library = path == library or library in path.parents
    if in_library and not any(path == d or d in path.parents for d in USER_LIBRARY):
        return "system"
    data = lantern_home().expanduser().resolve()
    if path == data or data in path.parents:
        return "lantern"
    return None


REFUSAL_MESSAGES = {
    "disk": "the whole disk cannot be indexed; choose the folders with your files (Documents, Desktop, a project)",
    "system": "system folders cannot be indexed: they hold no files of yours",
    "lantern": "Lantern's own data folder cannot be indexed",
}


def check(path: Path) -> None:
    """Raises ValueError with a plain message when the folder is refused."""
    reason = refusal(path)
    if reason:
        raise ValueError(REFUSAL_MESSAGES[reason])


@dataclass
class Estimate:
    path: str
    files: int = 0
    bytes: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    complete: bool = True  # False: the walk ran out of time, the numbers are a lower bound
    minutes: int = 0  # rough indexing time without recordings
    recordings: int = 0
    external: bool = False  # on another disk than the system (may be unplugged; a large one deserves a question)
    large: bool = False
    refused: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def estimate(path: Path, walk_options: dict | None = None, budget_s: float = ESTIMATE_BUDGET_S) -> Estimate:
    """Count what indexing `path` would read (the scanner's own rules), within a time budget."""
    est = Estimate(path=str(path), refused=refusal(path))
    if est.refused:
        return est
    with suppress(OSError):
        est.external = os.stat(path).st_dev != os.stat(Path.home()).st_dev
    deadline = time.monotonic() + budget_s
    seconds = 0.0
    for entry in iter_files(path, WalkReport(), **(walk_options or {})):
        est.files += 1
        est.bytes += entry.size
        est.by_kind[entry.kind] = est.by_kind.get(entry.kind, 0) + 1
        if entry.kind in MEDIA_KINDS:
            est.recordings += 1
        else:
            seconds += SECONDS_PER_FILE.get(entry.kind, SECONDS_PER_TEXT_FILE)
        if est.files % 256 == 0 and time.monotonic() > deadline:
            est.complete = False
            break
    est.minutes = round(seconds / 60)
    est.large = not est.complete or est.files >= LARGE_FILES or est.bytes >= LARGE_BYTES or (
        est.external and est.files >= LARGE_FILES // 4)  # fmt: skip
    return est

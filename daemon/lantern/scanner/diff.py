"""Scan a root and reconcile it with the index. The scan is the source of truth.

- unchanged (path, size, mtime) -> nothing
- size/mtime changed, same content hash -> metadata update only
- content changed / new file -> enqueue an index job
- new path with the hash of a vanished file -> move: repoint the record, keep chunks and vectors
- vanished files -> delete with all chunks, FTS rows and vectors
- root missing or unreadable -> no deletions at all (unmounted disk, network share)
"""

import hashlib
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from lantern.config import MASS_DELETE_FRACTION, MASS_DELETE_MIN_FILES, PARTIAL_HASH_BYTES
from lantern.queue import jobs
from lantern.scanner.walk import FileEntry, WalkReport, iter_files
from lantern.store import db


class RootUnavailable(Exception):
    pass


class MassDeletionRefused(Exception):
    def __init__(self, missing: int, total: int):
        super().__init__(f"{missing} of {total} indexed files are missing; refusing to purge without force")
        self.missing, self.total = missing, total


@dataclass
class ScanResult:
    new: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    touched: list[str] = field(default_factory=list)  # mtime changed, content identical
    moved: list[tuple[str, str]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: int = 0
    unreadable: list[str] = field(default_factory=list)  # folders without read permission (entries kept)
    cloud_only: int = 0  # iCloud files/folders not on the disk (skipped; entries kept)


_SAMPLE = 4 * 1024 * 1024


def file_hash(path: Path) -> str:
    """Full content hash; for files above PARTIAL_HASH_BYTES (videos) size + head/middle/tail samples, which is
    enough for move detection and change detection of media, and avoids reading gigabytes on every scan."""
    h = hashlib.blake2b(digest_size=20)
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > PARTIAL_HASH_BYTES:
            h.update(b"partial:%d:" % size)
            for offset in (0, size // 2 - _SAMPLE // 2, size - _SAMPLE):
                f.seek(offset)
                h.update(f.read(_SAMPLE))
        else:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
    return h.hexdigest()


def root_available(root: Path) -> bool:
    return root.is_dir() and os.access(root, os.R_OK | os.X_OK)


def scan_root(
    conn: sqlite3.Connection,
    root: Path,
    *,
    force: bool = False,
    excludes: list[str] | tuple[str, ...] = (),
    include_images: bool = True,
    skip_extensions: list[str] | tuple[str, ...] = (),
    include_media: bool = True,
) -> ScanResult:
    root = root.expanduser().resolve()
    if not root_available(root):
        raise RootUnavailable(f"root not available: {root}")

    root_id = db.ensure_root(conn, root)
    report = WalkReport()
    entries = iter_files(root, report, excludes, include_images, skip_extensions, include_media)
    disk: dict[str, FileEntry] = {str(e.path): e for e in entries}
    indexed = {
        r["path"]: r
        for r in conn.execute("SELECT id, path, size, mtime_ns, content_hash FROM files WHERE root_id = ?", (root_id,))
    }

    # Re-check availability after the walk: a disk unplugged mid-scan looks like "everything vanished".
    if not root_available(root):
        raise RootUnavailable(f"root disappeared during scan: {root}")

    # Not seen on disk, and not merely unreadable or evicted to iCloud: really gone.
    missing = {p: r for p, r in indexed.items() if p not in disk and not report.protects(p)}
    # The guard is for files that vanished (unmounted disk, wrong root). Files still on the disk were left out on
    # purpose (an exclusion, a switched-off file type) and may go without force.
    vanished = sum(1 for p in missing if not os.path.lexists(p))
    if not force and vanished >= MASS_DELETE_MIN_FILES and vanished > MASS_DELETE_FRACTION * len(indexed):
        raise MassDeletionRefused(vanished, len(indexed))

    result = ScanResult(unreadable=[str(p) for p in report.unreadable], cloud_only=report.cloud_only)
    missing_by_hash: dict[str, list[sqlite3.Row]] = {}
    for row in missing.values():
        missing_by_hash.setdefault(row["content_hash"], []).append(row)

    with db.transaction(conn):
        for path, entry in disk.items():
            row = indexed.get(path)
            if row is not None:
                if row["size"] == entry.size and row["mtime_ns"] == entry.mtime_ns:
                    result.unchanged += 1
                    continue
                digest = file_hash(entry.path)
                conn.execute(
                    "UPDATE files SET size = ?, mtime_ns = ?, content_hash = ? WHERE id = ?",
                    (entry.size, entry.mtime_ns, digest, row["id"]),
                )
                if digest == row["content_hash"]:
                    result.touched.append(path)
                else:
                    conn.execute("UPDATE files SET status = 'pending' WHERE id = ?", (row["id"],))
                    jobs.enqueue(conn, row["id"], "index")
                    result.changed.append(path)
                continue

            digest = file_hash(entry.path)
            candidates = missing_by_hash.get(digest)
            if candidates:  # move / rename
                old = candidates.pop()
                missing.pop(old["path"])
                rel = entry.path.relative_to(root).as_posix()
                db.update_file_path(conn, old["id"], path, rel)
                conn.execute(
                    "UPDATE files SET size = ?, mtime_ns = ? WHERE id = ?", (entry.size, entry.mtime_ns, old["id"])
                )
                result.moved.append((old["path"], path))
                continue

            cur = conn.execute(
                "INSERT INTO files(root_id, path, size, mtime_ns, content_hash, kind) VALUES (?, ?, ?, ?, ?, ?)",
                (root_id, path, entry.size, entry.mtime_ns, digest, entry.kind),
            )
            jobs.enqueue(conn, cur.lastrowid, "index")
            result.new.append(path)

        db.delete_files(conn, [r["id"] for r in missing.values()])
        result.deleted.extend(missing)
        conn.execute("UPDATE roots SET last_scan = ? WHERE id = ?", (db.now(), root_id))
    return result

"""SQLite job queue. Enqueue is idempotent, every job can be re-run from scratch, failures are isolated."""

import sqlite3
from dataclasses import dataclass

from lantern.store.db import now

MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Job:
    id: int
    file_id: int
    kind: str
    attempts: int


def enqueue(conn: sqlite3.Connection, file_id: int, kind: str) -> None:
    """Queue (or re-queue) work for a file; a duplicate enqueue is a no-op apart from resetting the retry count."""
    conn.execute(
        """INSERT INTO jobs(file_id, kind, status, attempts, updated_at) VALUES (?, ?, 'queued', 0, ?)
           ON CONFLICT(file_id, kind) DO UPDATE SET status = 'queued', attempts = 0, last_error = NULL,
           updated_at = excluded.updated_at""",
        (file_id, kind, now()),
    )


def recover(conn: sqlite3.Connection) -> int:
    """Jobs left 'running' by a crashed process go back to the queue."""
    return conn.execute("UPDATE jobs SET status = 'queued' WHERE status = 'running'").rowcount


def claim(conn: sqlite3.Connection, kind: str, limit: int) -> list[Job]:
    rows = conn.execute(
        "SELECT id, file_id, kind, attempts FROM jobs WHERE status = 'queued' AND kind = ? ORDER BY id LIMIT ?",
        (kind, limit),
    ).fetchall()
    if rows:
        marks = ",".join("?" * len(rows))
        conn.execute(
            f"UPDATE jobs SET status = 'running', updated_at = ? WHERE id IN ({marks})",
            (now(), *[r["id"] for r in rows]),
        )
    return [Job(r["id"], r["file_id"], r["kind"], r["attempts"]) for r in rows]


def complete(conn: sqlite3.Connection, job: Job) -> None:
    conn.execute("DELETE FROM jobs WHERE id = ?", (job.id,))


def fail(conn: sqlite3.Connection, job: Job, error: str) -> bool:
    """Record a failure. Returns True when the job is given up (attempt limit reached)."""
    attempts = job.attempts + 1
    give_up = attempts >= MAX_ATTEMPTS
    conn.execute(
        "UPDATE jobs SET status = ?, attempts = ?, last_error = ?, updated_at = ? WHERE id = ?",
        ("failed" if give_up else "queued", attempts, error[:1000], now(), job.id),
    )
    return give_up


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")}

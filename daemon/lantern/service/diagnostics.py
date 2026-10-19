"""A diagnostics report to paste into a bug report or an AI chat: Markdown with `key: value` lines, readable by people
and models alike. Versions, this Mac's memory, settings, index counts, models and the daemon's state; never file names,
folder names, file contents or queries (paths in warnings are replaced by <path>). Lantern sends it nowhere: the user
copies it (Settings → About, `lantern diagnostics`).
"""

import platform
import re
import sqlite3
import time

from lantern import __version__, config
from lantern.service import machine
from lantern.service import settings as settings_mod

# from the start of a path to the end of the line: names may contain spaces ("Tajny projekt/umowa.txt"), and in
# Lantern's warnings the path always comes last
_PATH = re.compile(r"(?:~|/Users|/Volumes|/private|/tmp|/var)/[^\n]*")
SETTINGS_SHOWN = ("index_mode", "mode", "pause_on_battery", "pause_on_low_power", "pause_on_thermal", "index_photos",
                  "index_image_text", "index_audio_video", "transcribe", "index_video_frames", "places", "people",
                  "notify")  # fmt: skip


def redact(text: str) -> str:
    return _PATH.sub("<path>", text)


def _chip() -> str:
    value = machine._sysctl("machdep.cpu.brand_string", __import__("ctypes").c_char * 128)
    return value.value.decode() if value else platform.machine()


def report(status: dict | None, conn: sqlite3.Connection | None) -> str:
    """status: the daemon's api_status (None without a daemon); conn: the index (None when it cannot be opened)."""
    mem = machine.read()
    s = settings_mod.load()
    pressure = {1: "normal", 2: "warn", 4: "critical"}.get(mem.pressure, str(mem.pressure))
    lines = [
        "# Lantern diagnostics",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M')} (local time). No file or folder names, contents or queries.",
        "",
        "## Version",
        f"- lantern: {__version__}",
        f"- macos: {platform.mac_ver()[0]}",
        f"- chip: {_chip()}",
        f"- python: {platform.python_version()}",
        "",
        "## Memory",
        f"- ram_gb: {mem.total / machine.GB:.0f}",
        f"- profile: {machine.profile(mem.total)}",
        f"- pressure: {pressure}",
        f"- free_percent: {mem.free_percent}",
        f"- swap_used_gb: {mem.swap_used / machine.GB:.1f}",
        "",
        "## Settings",
        *(f"- {k}: {getattr(s, k)}" for k in SETTINGS_SHOWN),
        f"- excluded_patterns: {len(s.exclude)} (not listed: they name folders)",
        f"- ignored_extensions: {', '.join(s.exclude_extensions) or 'none'}",
        "",
        "## Index",
    ]
    db_path = config.db_path()
    if db_path.exists():
        lines.append(f"- size_mb: {db_path.stat().st_size / 2**20:.0f}")
    if conn is not None:
        try:
            lines += [
                f"- schema: {conn.execute('PRAGMA user_version').fetchone()[0]}",
                f"- roots: {conn.execute('SELECT count(*) FROM roots').fetchone()[0]}",
                "- files_by_status: " + ", ".join(f"{k} {v}" for k, v in conn.execute(
                    "SELECT status, count(*) FROM files GROUP BY status ORDER BY status")),  # fmt: skip
                "- files_by_kind: " + ", ".join(f"{k} {v}" for k, v in conn.execute(
                    "SELECT kind, count(*) FROM files GROUP BY kind ORDER BY kind")),  # fmt: skip
                f"- chunks: {conn.execute('SELECT count(*) FROM chunks').fetchone()[0]}",
                "- jobs: " + (", ".join(f"{k}/{st} {n}" for k, st, n in conn.execute(
                    "SELECT kind, status, count(*) FROM jobs GROUP BY kind, status")) or "none"),  # fmt: skip
            ]
        except sqlite3.DatabaseError as exc:
            lines.append(f"- error: {redact(str(exc))}")
    else:
        lines.append("- could not be opened")
    lines += ["", "## Models"]
    try:
        from lantern import models as models_mod

        lines += [f"- {m.key}: {'downloaded' if models_mod.is_downloaded(m.repo, m.revision) else 'not downloaded'}"
                  for m in models_mod.catalogue()]  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - a report must still come out
        lines.append(f"- error: {redact(str(exc))}")
    lines += ["", "## Daemon"]
    if status is None:
        lines.append("- running: no")
    else:
        w = status.get("worker", {})
        work = f"{w.get('stage', 'running')} {w.get('done', '')}/{w.get('total', '')}".strip() if w.get("running") else "idle"  # noqa: E501
        lines += [
            "- running: yes",
            f"- memory_mb: {status.get('memory_mb')}",
            f"- paused: {status.get('paused') or 'no'}",
            f"- worker: {work}",
            f"- search_mode: {status.get('search_mode', s.mode)}",
            f"- model_loaded: {status.get('model_loaded')}",
            f"- model_reload_ms: {status.get('model_reload_ms')}",
            f"- models_missing: {status.get('models_missing')}",
            f"- index_problem: {status.get('index_problem') or 'none'}",
            f"- dates_pending: {status.get('dates_pending')}",
            f"- people_pending: {status.get('people_pending')}",
            f"- failed_jobs: {status.get('failed_jobs')}",
        ]
        warnings = status.get("warnings") or []
        lines += ["", "## Recent warnings (paths removed)"]
        lines += [f"- {redact(w)}" for w in warnings] or ["- none"]
    return "\n".join(lines) + "\n"


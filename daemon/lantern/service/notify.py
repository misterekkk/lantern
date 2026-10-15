"""macOS notifications via osascript (only when enabled in settings). Text is passed as an argument, never
interpolated into the script."""

import contextlib
import subprocess

_SCRIPT = "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run"


def notify(title: str, message: str) -> None:
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["osascript", "-e", _SCRIPT, title, message], capture_output=True, timeout=5)

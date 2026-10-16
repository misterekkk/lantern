"""The user's macOS language (first of AppleLanguages), used to give short photo queries a language cue."""

import os
import re
import subprocess
from functools import cache


@cache
def system_language() -> str:
    try:
        out = subprocess.run(
            ["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True, timeout=3
        ).stdout
        m = re.search(r'"?([a-z]{2})(?:[-_][A-Za-z]+)?"?', out)
        if m:
            return m.group(1)
    except (OSError, subprocess.SubprocessError):
        pass
    return (os.environ.get("LANG") or "en")[:2].lower()

"""Install / uninstall the per-user LaunchAgent (runs `lantern daemon run` at login, background priority)."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from lantern import config

LABEL = "com.lantern.daemon"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def render() -> bytes:
    logs = config.lantern_home() / "logs"
    env = {**config.OFFLINE_ENV, "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}
    if "LANTERN_HOME" in os.environ:
        env["LANTERN_HOME"] = os.environ["LANTERN_HOME"]
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": [sys.executable, "-m", "lantern.cli", "daemon", "run"],
            "EnvironmentVariables": env,
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},  # restart after a crash, not after `lantern daemon stop`
            "ProcessType": "Background",
            "LowPriorityIO": True,
            "Nice": 5,
            "StandardOutPath": str(logs / "daemon.log"),
            "StandardErrorPath": str(logs / "daemon.err.log"),
        }
    )


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install() -> Path:
    (config.lantern_home() / "logs").mkdir(parents=True, exist_ok=True)
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        subprocess.run(["launchctl", "bootout", _domain(), str(path)], capture_output=True)
    path.write_bytes(render())
    subprocess.run(["launchctl", "bootstrap", _domain(), str(path)], check=True)
    return path


def uninstall() -> bool:
    path = plist_path()
    if not path.exists():
        return False
    subprocess.run(["launchctl", "bootout", _domain(), str(path)], capture_output=True)
    path.unlink()
    return True

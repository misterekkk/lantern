"""Lantern in Alfred: a Script Filter that asks the Lantern daemon (its Unix socket, one JSON line each way) and an
action that opens the chosen result where the Lantern app would (the line in your editor, the moment in QuickTime).
Runs with macOS's own python3 (Command Line Tools), standard library only; nothing leaves the Mac.

    python3 lantern_alfred.py search "<query>"   # Alfred Script Filter JSON on stdout
    python3 lantern_alfred.py open '<arg>'       # ↩: open at the line / moment
    python3 lantern_alfred.py reveal '<arg>'     # ⌘↩: show in Finder
    python3 lantern_alfred.py start '<arg>'      # ⌥↩: open from the start
"""

from __future__ import annotations  # macOS ships python3 3.9

import contextlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

SOCKET = Path.home() / "Library" / "Application Support" / "Lantern" / "lantern.sock"
QUICKTIME_TYPES = {".mp4", ".mov", ".m4v", ".m4a", ".mp3", ".wav", ".aiff", ".aif", ".caf", ".flac"}


class NotRunning(Exception):
    pass


def call(method: str, params: dict, timeout: float = 20) -> dict:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(SOCKET))
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        raise NotRunning from exc
    with sock:
        sock.sendall(json.dumps({"id": 1, "method": method, "params": params}).encode() + b"\n")
        buf = bytearray()
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    msg = json.loads(buf)
    if msg.get("error"):
        raise RuntimeError(msg["error"]["message"])
    return msg["result"]


def short(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home) :] if path.startswith(home + "/") else path


def clock(ms: int) -> str:
    s = ms // 1000
    h, m = s // 3600, s % 3600 // 60
    return f"{h}:{m:02d}:{s % 60:02d}" if h else f"{m}:{s % 60:02d}"


def where(hit: dict) -> str:
    if hit.get("start_ms") is not None:
        return clock(hit["start_ms"])
    if hit.get("page") is not None:
        return f"page {hit['page']}"
    start, end = hit.get("start_line"), hit.get("end_line")
    if start is not None:
        return f"lines {start}–{end}" if end and end != start else f"line {start}"
    return ""


def item(path: str, title: str, subtitle: str, payload: dict, icon: dict | None = None) -> dict:
    arg = json.dumps(payload)
    return {
        "uid": f"{path}#{payload.get('line')}#{payload.get('ms')}",
        "title": title,
        "subtitle": subtitle,
        "arg": arg,
        "type": "file:skipcheck",
        "quicklookurl": path,
        "icon": icon or {"type": "fileicon", "path": path},
        "mods": {
            "cmd": {"arg": arg, "subtitle": "Show in Finder"},
            "alt": {"arg": arg, "subtitle": "Open from the start"},
        },
    }


def search(query: str) -> dict:
    query = query.strip()
    if not query:
        return {
            "items": [
                {
                    "title": "Search your Mac by what is inside",
                    "subtitle": "Files, code, photos and recordings, found by meaning",
                    "valid": False,
                    "icon": {"path": "icon.png"},
                }
            ]
        }
    try:
        r = call("search", {"query": query, "limit": 20})
    except NotRunning:
        return {
            "items": [
                {
                    "title": "Lantern is not running",
                    "subtitle": "Start the Lantern app; its background daemon answers these searches",
                    "valid": False,
                }
            ]
        }
    except Exception as exc:  # shown in Alfred instead of a silent empty list
        return {"items": [{"title": "Lantern could not search", "subtitle": str(exc), "valid": False}]}
    event = r.get("event_id")
    items: list[dict] = []
    order = list(dict.fromkeys([*r.get("order", []), "definitions", "files", "photos"]))
    for name in order:
        if name == "definitions":
            for d in r.get("definitions", []):
                title = f"{d['parent']}.{d['name']}" if d.get("parent") else d["name"]
                payload = {"path": d["path"], "line": d["line"], "ms": None, "event": event, "chunk": d.get("chunk_id")}
                items.append(
                    item(d["path"], title, f"{short(d['path'])} · line {d['line']}", payload, {"path": "icon.png"})
                )
        elif name in ("files", "photos"):
            for h in r.get("results" if name == "files" else "photos", []):
                at = where(h)
                line = h["start_line"] if h.get("start_line") is not None else -1
                key = f"{h['kind']}|{h['chunk_id']}|{line}|{h['path']}|"  # the app's row id (LanternCore SearchResult)
                payload = {
                    "path": h["path"],
                    "line": h.get("start_line"),
                    "ms": h.get("start_ms"),
                    "event": event,
                    "chunk": h["chunk_id"],
                    "key": key,
                }
                sub = " · ".join(x for x in (at, short(str(Path(h["path"]).parent))) if x)
                icon = {"path": h["path"]} if name == "photos" else None
                items.append(item(h["path"], Path(h["path"]).name, sub, payload, icon))
    if not items:
        items.append({"title": "No results in Lantern", "subtitle": query, "valid": False})
    return {"items": items, "skipknowledge": True}  # Lantern orders the rows; Alfred's own learning would reorder them


def run(*args: str, env: dict | None = None) -> bool:
    try:
        return subprocess.run(args, env={**os.environ, **(env or {})}, capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


DEFAULT_APP = """function run(argv) {
  ObjC.import('AppKit');
  const url = $.NSWorkspace.sharedWorkspace.URLForApplicationToOpenURL($.NSURL.fileURLWithPath(argv[0]));
  if (!url || url.isNil()) return '';
  const bundle = $.NSBundle.bundleWithURL(url);
  return [bundle.bundleIdentifier.js || '', url.path.js, bundle.executablePath.js || ''].join('\\n');
}"""

QUICKTIME = """on run argv
  set f to POSIX file (item 1 of argv)
  set t to (item 2 of argv) as integer
  tell application "QuickTime Player"
    activate
    set d to open f
    if d is missing value then set d to document 1
    set current time of d to t
    play d
  end tell
end run"""


def default_app(path: str) -> tuple[str, str, str]:
    out = (
        subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", "-e", DEFAULT_APP, path], capture_output=True, text=True
        )
        .stdout.strip()
        .split("\n")
    )
    return (out + ["", "", ""])[:3] if out != [""] else ("", "", "")


def open_at(path: str, line: int | None, ms: int | None) -> None:
    """In the app macOS would open the file with, never a different one (app/Sources/LanternCore/Editors.swift)."""
    quicktime = ms is not None and Path(path).suffix.lower() in QUICKTIME_TYPES
    if quicktime and run("/usr/bin/osascript", "-e", QUICKTIME, path, str(ms // 1000)):
        return
    if line is not None:
        bundle_id, app, executable = default_app(path)
        url = None
        if bundle_id in ("com.microsoft.VSCode", "com.microsoft.VSCodeInsiders"):
            url = "vscode"
        elif bundle_id == "com.vscodium":
            url = "vscodium"
        elif bundle_id == "com.todesktop.230313mzl4w4u92":
            url = "cursor"
        if url:
            from urllib.parse import quote

            if run("/usr/bin/open", f"{url}://file{quote(path)}:{line}"):
                return
        developer = {"DEVELOPER_DIR": f"{app}/Contents/Developer"}
        if bundle_id == "com.apple.dt.Xcode" and run("/usr/bin/xed", "-l", str(line), path, env=developer):
            return
        jetbrains = bundle_id.startswith("com.jetbrains.") or bundle_id == "com.google.android.studio"
        if jetbrains and executable and run(executable, "--line", str(line), path):
            return
    run("/usr/bin/open", path)


def opened(p: dict) -> None:
    """Lantern learns your choices from Alfred as from its own panel."""
    if p.get("event") is None or p.get("chunk") is None:
        return
    params = {"event_id": p["event"], "chunk_id": p["chunk"]}
    if p.get("key"):
        params["key"] = p["key"]
    with contextlib.suppress(Exception):  # never block opening on the history
        call("opened", params, timeout=3)


def main(argv: list[str]) -> int:
    mode, value = (argv + ["", ""])[1:3]
    if mode == "search":
        print(json.dumps(search(value)))
        return 0
    payload = json.loads(value)
    opened(payload)
    if mode == "reveal":
        run("/usr/bin/open", "-R", payload["path"])
    elif mode == "start":  # ⌥↩: the file from its beginning; a recording seeks to 0:00 even if already open later on
        open_at(payload["path"], None, 0 if payload.get("ms") is not None else None)
    else:
        open_at(payload["path"], payload.get("line"), payload.get("ms"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

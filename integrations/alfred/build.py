"""Builds Lantern.alfredworkflow (a zip Alfred imports on double-click): a Script Filter on the keyword "l" (a hotkey
can be added in Alfred), ↩ opens at the line or moment, ⌘↩ shows in Finder. Usage: python3 build.py [out_dir]"""

import plistlib
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
FILTER, OPEN, REVEAL, START = (
    "6E1F0B5A-0C1D-4F57-9A66-3B1F2C6D7E01",
    "6E1F0B5A-0C1D-4F57-9A66-3B1F2C6D7E02",
    "6E1F0B5A-0C1D-4F57-9A66-3B1F2C6D7E03",
    "6E1F0B5A-0C1D-4F57-9A66-3B1F2C6D7E04",
)
CMD, ALT = 1048576, 524288  # Alfred's modifier masks for ⌘ and ⌥


def script(uid: str, mode: str) -> dict:
    return {
        "uid": uid,
        "type": "alfred.workflow.action.script",
        "version": 2,
        "config": {
            "type": 0,
            "scriptargtype": 1,
            "escaping": 0,
            "concurrently": False,
            "script": f'/usr/bin/python3 ./lantern_alfred.py {mode} "$1"',
            "scriptfile": "",
        },
    }


def workflow() -> dict:
    return {
        "bundleid": "com.lantern.alfred",
        "name": "Lantern",
        "description": "Search your Mac by what is inside, with Lantern",
        "createdby": "Lantern",
        "webaddress": "https://misterekkk.github.io/lantern/",
        "readme": "Needs the Lantern app running (its daemon answers the searches). Type l and your query; "
        "↩ opens at the line or moment, ⌥↩ from the start, ⌘↩ shows in Finder, ⇧ or ⌘Y Quick Look. "
        "Nothing leaves your Mac.",
        "category": "Productivity",
        "version": "1.0",
        "objects": [
            {
                "uid": FILTER,
                "type": "alfred.workflow.input.scriptfilter",
                "version": 3,
                "config": {
                    "keyword": "l",
                    "withspace": True,
                    "argumenttype": 1,
                    "title": "Search Lantern",
                    "subtext": "Files, code, photos and recordings, by what is inside",
                    "runningsubtext": "Searching…",
                    "type": 0,
                    "scriptargtype": 1,
                    "escaping": 0,
                    "script": '/usr/bin/python3 ./lantern_alfred.py search "$1"',
                    "scriptfile": "",
                    "alfredfiltersresults": False,
                    "queuemode": 2,
                    "queuedelaymode": 0,
                    "queuedelaycustom": 3,
                    "queuedelayimmediatelyinitially": False,
                    "argumenttrimmode": 0,
                    "argumenttreatemptyqueryasnil": True,
                },
            },
            script(OPEN, "open"),
            script(REVEAL, "reveal"),
            script(START, "start"),
        ],
        "connections": {
            FILTER: [
                {"destinationuid": OPEN, "modifiers": 0, "modifiersubtext": "", "vitoclose": False},
                {"destinationuid": REVEAL, "modifiers": CMD, "modifiersubtext": "Show in Finder", "vitoclose": False},
                {
                    "destinationuid": START,
                    "modifiers": ALT,
                    "modifiersubtext": "Open from the start",
                    "vitoclose": False,
                },
            ]
        },
        "uidata": {
            FILTER: {"xpos": 40, "ypos": 60},
            OPEN: {"xpos": 300, "ypos": 20},
            REVEAL: {"xpos": 300, "ypos": 140},
            START: {"xpos": 300, "ypos": 260},
        },
    }


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else HERE / "dist") / "Lantern.alfredworkflow"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("info.plist", plistlib.dumps(workflow()))
        for name in ("lantern_alfred.py", "icon.png"):
            z.write(HERE / name, name)
    print(out)


if __name__ == "__main__":
    main()

# Lantern for Alfred

> **Not tested inside Alfred yet** (workflows need Alfred's paid Powerpack). The script itself is tested against the
> daemon; if the workflow does not import or behave in Alfred, please open an issue.

A workflow (Alfred 5 with the Powerpack): type `l` and your query to see Lantern's results (code definitions, files
at the line, page or moment, photos). The Lantern app must be running; the workflow asks its daemon over the local
Unix socket with macOS's own `python3` (standard library only), and nothing leaves the Mac.

| Key | Action |
|---|---|
| ↩ | open at the line (VS Code, VSCodium, Cursor, Xcode, JetBrains) or play from the moment (QuickTime) |
| ⌥↩ | open from the start |
| ⌘↩ | show in Finder |
| ⇧ / ⌘Y | Quick Look |

## Install

- From the app: Settings → General → Search from: **Alfred** → *Install the Alfred Workflow…* (built into the app).
- From the repository: `python3 integrations/alfred/build.py` writes `integrations/alfred/dist/Lantern.alfredworkflow`;
  open it to import.

In Alfred you can change the keyword or add a hotkey (Workflows → Lantern). Rows keep Lantern's order: the workflow
switches off Alfred's own reordering (`skipknowledge`).

`lantern_alfred.py` is the whole workflow: `search "<query>"` prints Alfred's Script Filter JSON, `open` / `reveal`
act on the chosen row.

# Lantern for Raycast

"Search Lantern" in Raycast: Lantern's results (code definitions, files at the line, page or moment, photos) in
Raycast's window. The Lantern app must be running; its daemon answers over its local Unix socket, and nothing leaves
the Mac (Raycast's own features follow Raycast's privacy policy).

| Key | Action |
|---|---|
| ↩ | open at the line (VS Code, VSCodium, Cursor, Xcode, JetBrains) or play from the moment (QuickTime) |
| ⌘↩ | show in Finder |
| ⌘Y | Quick Look |
| ⇧⌘C | copy the path |

Preference: *Also show apps and System Settings* (off: Raycast finds those itself).

## Install (until it is in the Raycast Store)

Needs Node.js (`brew install node`).

```bash
cd integrations/raycast
npm ci
npm run dev        # adds "Search Lantern" to Raycast and rebuilds on changes; Ctrl+C stops watching, the command stays
```

Then in Lantern: Settings → General → Search from: **Raycast** (turns Lantern's own shortcut off), and in Raycast's
settings give "Search Lantern" a hotkey, e.g. ⌥Space.

`npm run build` type-checks and bundles into `dist/`.

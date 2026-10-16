# App (Swift menu-bar client)

A **thin client of the daemon API** (Unix socket, JSON lines): no indexing, embedding or database logic here.

## Layout

- `Sources/LanternCore/` — `DaemonClient` (POSIX Unix socket, one request per connection), Codable models mirroring
  `daemon/lantern/service/core.py` (`api_status`, `api_search`, `config_get`), `Format` helpers. Unit-tested.
- `Sources/Lantern/` — `LanternApp` (MenuBarExtra), `AppState` (status polling every 2 s, actions, notifications),
  `DaemonSupervisor` (starts the Python daemon as a child process so macOS file permissions belong to the app;
  uses an already running daemon instead), `SearchPanel` (borderless NSPanel, ⌥Space via Carbon `HotKey`),
  `MenuView`, `SettingsView` (own window: SwiftUI's Settings scene is unreliable in LSUIElement apps),
  `Thumbnails` (Quick Look / AVFoundation thumbnails for photo and video-frame results, cached in memory only).
- Opening a result: `EditorOpener` asks macOS for the default app of the file and uses `LineJump` (LanternCore,
  unit-tested) to jump to the line: `vscode://` / `cursor://` URLs, `xed -l` with `DEVELOPER_DIR` set to that
  Xcode (xcode-select may point at the Command Line Tools), JetBrains launchers with `--line`; anything else opens
  the file normally. Never switch to a different app than the user's default.
- Settings: `SettingsView` is a `NavigationSplitView` (sidebar panes like System Settings): General, Folders
  (`FolderTree` with lazy `TreeNode`s and name search, `ExtensionField` pills — parsing in LanternCore
  `ExtensionInput`), Performance (`PerformanceView`, Swift Charts, polls `perf` every second while visible),
  Privacy, About. In grouped forms use `TextField("", text:, prompt:)` + `.labelsHidden()`, and never put `**` in a
  string-literal `Text` (Markdown).
- Panel shortcuts go through `SearchModel.act(OpenAction)`: ↩ at location, ⌥↩ from start, ⌘Y preview inside the
  panel (`Preview.swift`: text range, `AVPlayerView` from the moment), ⌘↩ Finder. Recordings: `MediaOpener` runs
  osascript (QuickTime `current time`, whole seconds) and falls back to the preview; needs
  `NSAppleEventsUsageDescription`.
- Dev flags: `--settings-tab <pane>`, `--expand-tree`, `--tree-query <q>`, `--scroll-to extensions`, `--preview`.
- Search results come in sections (`SearchResponse.sections`: photos and files, order decided by the daemon); the
  panel keeps one flat selection index across sections.
- `Resources/Info.plist.template` — bundle id `com.lantern.app`, `LSUIElement`, privacy usage strings (Polish),
  `LanternPython` (filled by build.sh).

## Commands

```bash
app/build.sh                                         # build + sign app/build/Lantern.app (Xcode toolchain)
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test --package-path app
# development: isolated daemon/index, panel kept open, open panel/menu/settings on launch
LANTERN_HOME=/tmp/x LANTERN_SOCKET=/tmp/l.sock LANTERN_PANEL_PINNED=1 \
  app/build/Lantern.app/Contents/MacOS/Lantern --show-search "query" | --show-menu | --show-settings
```

## Conventions

- Swift 5 language mode, macOS 14+; Liquid Glass (`glassEffect`) behind `#available(macOS 26, *)` with a material
  fallback. The Xcode toolchain is selected with `DEVELOPER_DIR` (Command Line Tools ship an older SDK).
- Localization follows the macOS language (English source strings as keys, `Resources/<lang>.lproj`:
  `Localizable.strings`, `Localizable.stringsdict` for plurals — Polish has one/few/many — and `InfoPlist.strings`
  for permission prompts). SwiftUI `Text`/`Button`/`Label` literals localize automatically; plain `String` values must
  use `String(localized:)`, and view parameters that hold UI text should be `LocalizedStringKey`. Adding a language =
  a new `.lproj` folder + its code in `CFBundleLocalizations`. Test another language with `-AppleLanguages "(en)"`.
- Animations: short and quiet (`.snappy`/`.smooth`, ≤ 0.3 s), no bouncing; respect the system look.
- When the daemon API changes, update the models here and the decoding tests in `Tests/LanternCoreTests`.
- Signing: `build.sh` uses the first "Apple Development" identity (stable signature keeps macOS permissions across
  rebuilds), otherwise ad-hoc. The Apple WWDR G3 intermediate must be in the keychain (it was missing on this Mac).

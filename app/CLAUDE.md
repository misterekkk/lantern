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
- Settings: `SettingsView` copies System Settings (`NavigationSplitView`, sidebar search + status row, panes in
  groups, ← → arrows, subpages in a `NavigationStack`). Panes are built from `SettingsKit.swift`: `PaneHeader`
  (icon, title, one-line description), plain text rows (no icons inside panes: they drew the eye away from the
  words; icons stay in the sidebar, pane headers and search results), `SettingsPage` (grouped form + soft
  scroll edge; `scrollEdgeEffectStyle` on macOS 26, gradient mask before). Dependent rows are dimmed, never indented.
  Panes: General (login, language via `AppLanguage` + relaunch, shortcut, index mode), File Types (switches,
  `ExtensionField` pills — parsing in LanternCore `ExtensionInput`), Folders (roots; subpages `TreePage` with
  `FolderTree` and toolbar search, `PatternsPage`), Indexing (`PowerPane`: pauses and the schedule; its title key is
  "Indexing (pane)" with en.lproj/pl.lproj texts, because "Indexing" is already the status "Indeksuje"), Performance (`PerformanceView`, Swift Charts,
  polls `perf` every second while visible), Privacy, About. The window needs `sceneBridgingOptions = [.title, .toolbars]`. In grouped forms use `TextField("", text:, prompt:)` + `.labelsHidden()`, and never put `**` in a
  string-literal `Text` (Markdown).
- Panel extras: scope icons beside the field (default; `ScopeIcon` / `ScopeChip`, Liquid Glass, `scopeStyle` = icons |
  pills) or filter pills (`ScopeBar`, `ResultCategory`; the daemon returns `categories` and filters with
  `category`), inline completion (`completion` from `search/suggest.py`, shown behind the field, → accepts), shortcut
  hints (`@AppStorage("shortcutHints")`: row / bar / off), matched query words in bold in each result
  (LanternCore `QueryHighlight` + `TextFold`, unit-tested; the demo site has a JS port). Test keys with `CGEvent.postToPid` (sends only to Lantern),
  never System Events (it types into the frontmost app). ⇧Tab arrives as U+0019. Corners are concentric: selection
  radius = panel radius 26 − list inset 8.
- Panel shortcuts go through `SearchModel.act(OpenAction)`: ↩ at location, ⌥↩ from start, ⌘Y preview inside the
  panel (`Preview.swift`: text range, `AVPlayerView` from the moment), ⌘↩ Finder; ⌘C copies the result (a file URL
  on the pasteboard), ⌥⌘C the matched passage (`passage` API), unless the field has a text selection (`SearchModel.copy`). Recordings: `MediaOpener` runs
  osascript (QuickTime `current time`, whole seconds) and falls back to the preview; needs
  `NSAppleEventsUsageDescription`.
- Welcome window (`WelcomeWindow.swift`, phase 17): first launch (`welcomeDone` in UserDefaults; set automatically
  for someone updating with folders and the model already there). Steps: welcome, usage mode, models (`setup` API:
  recommended by RAM profile), folders, launcher, done. `--welcome` shows it without recording it as done;
  `--welcome-snapshot <dir>` writes each step as PNG through ImageRenderer (with `LANTERN_FAKE_RAM_GB=8` for the
  8 GB variant, and a short `LANTERN_SOCKET` such as `/tmp/lw.sock`: socket paths are limited to 104 bytes).
- UI checks in every language: `--ui-snapshot <dir>` (menu states + welcome steps via ImageRenderer, no daemon; system
  controls become placeholders) and `--preview-state <menu-N-name>` (that state in the real menu, for a real capture
  of only Lantern's window with `screencapture -l <window id>`; never the whole screen). Run with
  `-AppleLanguages "(pl)"` / `"(en)"`. Text that may be longer in Polish needs `.fixedSize(horizontal: false,
  vertical: true)`: offline renders wrap it, the real menu truncated it.
- `lantern://preview?path=&ms=&text=` (`PreviewWindow.swift`): the panel's `PreviewView` for a recording in a small
  floating window, for Raycast's ⌘Y (Quick Look cannot seek); only indexed files (`file` API), ⌘Y / Esc close it.
  `--open-url <url>` calls the same handler (the installed app shares the bundle id, so `open lantern://…` would go
  to it, not to the dev build).
- Dev flags: `--settings-tab <pane>`, `--expand-tree`, `--tree-query <q>`, `--scroll-to <row id>` (`extensions`,
  `restore`, `data`), `--add-folder <path>` (the estimate and alerts), `--preview`,
  `--hover-scope <kind>`, `--unfold-scopes` (unfolds the folded scope icons 1.5 s after they fold). Clicks posted
  with `CGEvent.postToPid` do not reach SwiftUI buttons in the panel; use a dev flag instead.
- Scope icons fold into `FoldedScopes` when the query text reaches them (field width measured with a
  `GeometryReader` + `onChange`: preferences do not pass through `GlassEffectContainer`).
- Spelling: `Spelling.swift` (NSSpellChecker guesses in the user's macOS languages, not the app's) after a pause;
  `SpellingWords` (LanternCore) picks the words; the daemon decides (`search/spelling.py`, `corrected_query`). Text
  in the panel that should not size it goes in an overlay (`CorrectionLine`): a wide child made SwiftUI resize the
  window and `SearchPanel.setFrame` re-centre it in a loop that hung the app.
- Apple Music: `MusicLibrary.swift` reads the library with iTunesLibrary (`MusicLibrary.read`, dev flag `--music-dump`),
  `MusicSync` sends it to the daemon (`music` API) while `appleMusicLibrary` is on (launch with
  `-appleMusicLibrary YES` to test without touching the shared defaults), `MusicPlayer` plays via osascript.
- Settings → General → Search from (`SearchLauncher`: lantern | raycast | alfred): with a launcher the hotkey is not
  registered; `build.sh` builds `integrations/alfred` into Resources (`Lantern.alfredworkflow`).
- Search results come in sections (`SearchResponse.sections`: photos and files, order decided by the daemon); the
  panel keeps one flat selection index across sections.
- `Sources/LanternMedia` — the `lantern-media` helper (AVFoundation: `audio`, `frames`, `still`), copied into
  Contents/MacOS by build.sh and signed before the bundle; DaemonSupervisor passes its path as LANTERN_MEDIA.
- `Resources/Info.plist.template` — bundle id `com.lantern.app`, `LSUIElement`, privacy usage strings (Polish),
  `LanternPython` (filled by build.sh).

## Commands

```bash
app/build.sh                                         # build + sign app/build/Lantern.app (Xcode toolchain, uses .venv)
app/build.sh --dmg                                   # standalone Lantern.app (bundled Python 3.12) + app/build/Lantern.dmg
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test --package-path app
# development: isolated daemon/index, panel kept open, open panel/menu/settings on launch
LANTERN_HOME=/tmp/x LANTERN_SOCKET=/tmp/l.sock LANTERN_PANEL_PINNED=1 \
  app/build/Lantern.app/Contents/MacOS/Lantern --show-search "query" | --show-menu | --show-settings
```

## Conventions

- Swift 5 language mode; the app requires macOS 26.2 (`LSMinimumSystemVersion`: MLX's macOS 26 libraries need
  26.2; the release workflow checks every bundled library's minimum against it). Liquid Glass (`glassEffect`) behind
  `#available(macOS 26, *)` with a material
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

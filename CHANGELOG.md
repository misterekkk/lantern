# Changelog

What changed for people using Lantern, newest first. Lantern has no numbered releases yet: entries are dated and
`main` is always the current version.

## Unreleased (branch `development`)

- **Use with Raycast or Alfred:** Settings → General → *Search from* hands searching to Raycast (the "Search Lantern"
  extension) or Alfred (a workflow built into the app). Lantern's shortcut turns off; it keeps indexing and answers
  in the background, and your choices there teach it as in its own panel.
- **Raycast and Alfred commands in Lantern's panel:** the commands of your Raycast extensions and the keywords of your
  Alfred workflows appear under **Commands** (with your Shortcuts); ↩ opens them there.
- Raycast can show "Search Lantern" under any query (Fallback Commands).
- **Fixed:** changing the search shortcut (or handing it to Raycast or Alfred) left the old one working.

## 2026-10-17

- **Website:** https://misterekkk.github.io/lantern/ with a landing page, the docs and the interactive demo.
- **One letter + ↩ as fast as Spotlight:** apps you use most come first from day one (macOS's own launch counts), and
  names show in milliseconds while files follow a moment later.
- **Learned choices:** a query you keep answering with the same result puts it first, also for its first letters
  ("d" → Discord); Alfred's learned choices can be imported (Settings → Privacy).
- **⌥↩ on an app** opens a new window; ↩ brings the app forward as in Spotlight.
- **Music:** songs and playlists from Apple Music, and from Spotify through an opt-in sync with your own key; ↩ plays.
- **Text in video frames** is searchable; Whisper drops noise it used to transcribe as words.
- **Spelling:** a misspelt query is corrected when the index knows the right word.
- **New icon**, a calmer menu bar menu (a status dot, Settings at the bottom), Quit in Settings → About.
- Settings show values with a capital letter ("Not running · last run peak 1578 MB").

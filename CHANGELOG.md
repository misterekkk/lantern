# Changelog

What changed for people using Lantern, newest first.

## 0.1.2 — 2026-10-19

- **Requirements corrected: macOS 26.2 (Tahoe) or newer.** The app said macOS 14, but the MLX libraries inside it are
  built for macOS 26.2, so on older systems the app opened while search could not start. The app, the installer, the
  website and the docs now say 26.2, and every release checks that no bundled library needs a newer macOS than the app
  declares.
- **Raycast:** a PDF opened in Preview gets its page only once Preview is in front with the document open, so the
  Go to Page keystrokes can never reach another app.
- **Website:** `llms.txt` and `llms-full.txt` are generated from the README and the docs at every build (the earlier
  hand-written files described commands, files and formats Lantern does not have); the ineffective `robots.txt` is
  gone (crawlers read it only at the domain root); `--no-quarantine` is no longer suggested (Homebrew removed it).
- **Docs:** PRIVACY describes the in-app model download.

## 0.1.1 — 2026-10-17

- **Fixed (Raycast):** transcripts of recordings and videos now appear in Raycast's preview pane (`passage` returns the
  recording's transcript for audio-window and video-frame matches, and video/photo thumbnails leave room for the
  transcript above the metadata section).
- **Fixed (Settings):** *Settings → File Types → Transcribe speech (Whisper)* is now off and disabled until the optional
  Whisper model is downloaded in *Settings → Performance*, and the index worker skips transcription cleanly when Whisper
  is not installed instead of failing transcription jobs.
- **Docs:** removed the outdated `Text inside images is not read (no OCR)` bullet from *Known limitations* in `README.md`.

## 0.1.0 — 2026-10-17

- **Install from `Lantern.dmg`:** a self-contained app built on GitHub Actions (`macos-26` Apple Silicon) with Python
  3.12, MLX and the daemon bundled inside `Lantern.app` (`app/build.sh --dmg`, `.github/workflows/release.yml`). No
  Xcode, Homebrew, `uv` or `ffmpeg` is needed to run the app; on first launch the menu bar (and Settings → Performance)
  offers a one-click download for the required search model and optional Whisper and SigLIP 2 weights.
- **No PyTorch (~750 MB smaller):** SigLIP 2's image tower now runs directly on MLX (`float16`, cosine similarity
  `0.99999` vs the PyTorch reference; existing photo indexes do not re-index) and the EmbeddingGemma 2 processor is
  built from its numpy/PIL parts without importing `torch`.
- **Media decoded by macOS:** recordings and video frames (MP3, M4A, WAV, FLAC, MP4, MOV) are decoded with macOS's
  `AVFoundation` (`lantern-media` inside the app bundle); `ffmpeg` is now optional and only needed for MKV and WebM.
- **Use with Raycast or Alfred:** Settings → General → *Search from* hands searching to Raycast ("Search Lantern") or
  Alfred (a workflow built into the app). Lantern's shortcut turns off; it keeps indexing and answers in the
  background, and your choices there teach it as in its own panel. Step by step: README → Use with Raycast or Alfred.
- **In Raycast:** the panel's filters on ⌃1 … ⌃0, a preview beside the results (the code around the line, the
  passage with your words in bold, thumbnails of photos and video frames, type icons), ⌘Y to preview at the place,
  ⌥↩ to open from the start, music and Shortcuts; as a Fallback Command under every Raycast query. Raycast never
  asks for access to your folders.
- **Alfred:** not tested inside Alfred yet (workflows need the paid Powerpack); feedback welcome.
- **Fixed:** Spotify's Liked Songs did not play (in the panel and in Raycast).
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

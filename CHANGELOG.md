# Changelog

What changed for people using Lantern, newest first.

## Unreleased

- **Welcome window** on first launch, in the system's language: how often you search, which models (recommended for
  this Mac's memory), which folders, and Lantern's panel, Raycast or Alfred.
- **Careful with memory:** an 8 GB Mac starts light (search model only, Whisper and video frames off, the model
  released when idle); heavy work starts only when the Mac has room for it; indexing waits under memory pressure.
- **"When needed" mode** releases the search model after 10 idle minutes (`lantern config mode on_demand`), or only on
  battery (`battery`: always ready on the charger); the next search loads it in about half a second.
- **Lighter indexing on 8 GB Macs:** the models' memory limit follows the free memory and stays at 1 GB on 8 GB
  Macs (measured: 0.3-0.5 GB less per indexing process).
- **An index from a newer Lantern or a damaged one** is explained with Update / Rebuild instead of Lantern stopping.
- **Copy Diagnostics** (Settings → About, `lantern diagnostics`): a report for bug reports without file names.
- **CLI = app:** `lantern models` (list, download), `lantern rebuild`, `lantern languages`, and `--json` for
  `search`, `status`, `pending`, `config`, `models` and `languages`.
- **Hard to break:** the whole disk, system folders and `~/Library` are refused as folders; a very large new folder
  shows its estimate first (`lantern index --yes` adds it); a hand-edited `config.toml` with wrong values is
  corrected instead of stopping Lantern.
- **Indexing schedule:** any time, only when the Mac is idle, or at night (`lantern config schedule night`); search
  works at any hour.
- **Restore defaults and delete all data** (Settings → General and Privacy; `lantern restore-defaults`,
  `lantern delete-all-data`): folders stay on a restore; deleting removes the index, history, settings, the Spotify
  sign-in and logs, never your files or the models.
- **Settings → Indexing** (was Battery and Heat) with the schedule.
- **Raycast** explains a missing app, an unfinished setup, a model download and a first indexing. ⌘Y works as in
  the panel: a photo in Quick Look, a video or recording in Lantern's own player from the moment of the match (the
  whole file, so you can go back to the start), in a window the size of Raycast's with more of the transcript; ⌘Y
  closes the preview and stops the sound. The frame and transcript are on ⇧⌘Y.
- **Settings → Performance:** a taller memory chart, "Search model" (always ready / when needed) under the models, model
  sizes in Finder's units.
- **Icon:** Apple's continuous corners and shadow, no light rim; the status dot uses macOS's own amber.

## 0.2.0 — 2026-10-19

- **People in photos:** `zdjęcia kobiet`, `zdjęcie kobiety z 2024 w Portugalii`, `bez ludzi`, `grupa`, `selfie`,
  `dwie osoby`. macOS counts the people on your Mac and the photo model tells women from men by appearance; only the
  numbers are kept. Switch: Settings → File Types → People in photos.
- **Time and place:** `zdjęcia z maja 2023`, `umowa z 2024`, `photos from last summer`, `zdjęcia z Krakowa`,
  `Bilder aus Krakau`. Dates come from photos' and videos' own data, then dates in file names, then the disk; places
  from their saved location and a city table inside Lantern (nothing goes online). A time or a place alone lists what
  was made then or there. Photos and recordings show their date and city. Switch: Settings → File Types → Places in
  photos and videos. Updating fills dates in the background in a minute or two; nothing is indexed again.
- **Raycast ⌘Y on a video** previews inside Raycast, like the panel: the frame at the moment, large, above the
  transcript. It used to open QuickTime, which stayed open in front after the preview; playing stays on ↩.

## 0.1.3 — 2026-10-19

- **`in:folder`:** `in:down umowa`, `in:"Moje dokumenty" faktura` or `in:~/Projects/x readme` keeps results to files
  under that folder, in the panel, Raycast, Alfred and the CLI.
- **⌘C / ⌥⌘C in the panel and Raycast:** copy the selected file (as Finder and Spotlight do), or the matched text:
  the passage, the code around a definition, the text read from a photo, the transcript at the moment.
- **A stuck file never stops indexing:** after 30 minutes on one file (90 for a transcript) it is skipped with the
  reason, and the queue goes on.
- **Indexing pauses when less than 2 GB is free** on the disk of the index.
- **Fixed:** transcripts queued before transcription was switched off made the index worker start and exit again and
  again. The README now says exactly what switching transcription off does.
- **Raycast:** play from the start rewinds an open recording, the moment preview pauses a playing video, and a moment
  or page that cannot be set is reported instead of silently skipped; FLAC plays at the moment.
- **Faster search:** stored vectors are read straight from sqlite-vec's blocks, and the index through mmap (search
  by meaning about 40% faster).

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

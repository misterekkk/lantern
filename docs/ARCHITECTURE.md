# Architecture

## Overview

```
Swift app (menu bar)    CLI / scripts    Raycast extension · Alfred workflow (integrations/)
          └──── Unix socket (0600, user only) ───────────┐
                                                         ▼
Daemon (LaunchAgent or child of the app; text model in memory for search, ~490 MB)
  API · control loop · scanner + watcher (FSEvents) · ask/auto mode · pause (battery, heat)
        │ starts it when work is approved, stops it (pause)
  Index worker (background priority; exits when the queue is empty) · job queue (SQLite)
        │
  Extractors (text, code, PDF, audio, photos, frames; OCR via Apple Vision) → chunker → embedder (MLX, EmbeddingGemma 2 8-bit)
        │                                   ├─ audio/video: audio windows + transcripts (mlx-whisper)
        │                                   └─ photos/frames: EG2 vision tower + SigLIP 2 (second model)
        ▼
  SQLite (one file): metadata + FTS5 (words) + sqlite-vec (vectors: one table per model and per text/photos)
        + symbols (code definitions)
        ▲
  Search: definitions (symbol table) | files (vectors + BM25 → RRF) | photos (EG2 + SigLIP, z-scores)
          | apps, System Settings, shortcuts, music (names, words, meaning) | answers, dictionary
          → sections on one 0..1 scale → learned choices → search_events
```

The app adds its own sections on the same scale (clipboard, menu items and windows, the web row) and sends the
music libraries it reads (Apple Music through iTunesLibrary; Spotify through its Web API, the one opt-in network
connection, see PRIVACY.md).

All logic lives in the daemon. Clients only call the API.

## Principles

- **The scan is the source of truth.** FSEvents is only an optimisation, because events are lost while the daemon
  is not running. On start we compare (path, size, mtime) with the database and hash content only when needed.
- **Mass-deletion guard:** an unavailable root folder (unplugged disk, network share) never removes entries, and
  more than half of a folder vanishing needs `--force`. Files excluded on purpose (still on the disk) do not count.
- **Moves** are recognised by content hash, without recomputing embeddings.
- **Job queue** in SQLite: idempotent, resumable; one failing file never blocks the rest.
- **Embedders behind an interface**, a `model_id` on every vector, vectors of different models never mixed.
- **Hybrid search:** vectors alone miss function names and error messages, so they are fused with BM25 through RRF.
- **Audio:** AVFoundation (`lantern-media`, ffmpeg for MKV/WebM) to 16 kHz mono, 30 s windows with a 25 s hop, timestamps. Two tracks: audio embeddings and
  transcripts (words, previews). Rationale: DECISIONS.md, H1–H4.
- **Photos:** files and photos are different kinds of results, so they are not fused into one ranking; results come
  in sections and the section with the more confident list goes first (DECISIONS.md, Phase 7).
- **Code definitions** come from a tree-sitter symbol table and are shown first (DECISIONS.md, Phase 8).
- **RAM budget (16 GB):** the text tower stays loaded (~0.4–0.5 GB) and the photo query model (SigLIP, ~0.34 GB) is
  loaded on demand; the audio tower, Whisper, the vision tower and SigLIP for photos live only in the worker during
  jobs, and audio and Whisper are never loaded at the same time.

## Phases

| Phase | Scope | Status |
|---|---|---|
| 0 | backend survey, measurements, quality test | done (DECISIONS.md) |
| 1 | repository skeleton and documentation | done |
| 2 | core: schema, scanner, text/code extractor, chunker, embedder, CLI `index/search/status` | done |
| 3 | hybrid search (RRF), index hygiene, VACUUM, `search_events`, Polish FTS normalisation | done |
| 4 | audio (also the sound of videos via ffmpeg): windows, audio embeddings, transcripts, timestamps | done (evaluated on synthetic speech; real recordings: open question 14) |
| 5 | daemon + API (Unix socket), FSEvents, LaunchAgent, pause on battery and heat; "ask" mode: after start (also after a long shutdown) the scan finds new and changed files and asks whether to index them | done |
| 5b | whole Mac: home-folder policy, local-only iCloud, protection of folders without permission, user exclusions, `--dry-run`, scale test | done |
| 6 | Swift app: menu bar (on/off, status, queue, "found N new files, index them?"), Spotlight-style search panel on a global shortcut, design following Apple's HIG (SwiftUI, Liquid Glass, SF Symbols) | done |
| 7 | search in photos and video frames (ffmpeg → frames → EG2 vision tower + SigLIP 2), preceded by a quality and speed test; photos section in the panel | done |
| 8 | precise code search: tree-sitter symbol table (name, kind, signature, line), correct names in C/C++/Go and methods of small classes, "Definitions" section, opening at the line in the default editor | done |
| 9 | settings and media (plan below): folder tree with search, ignored extensions, Performance pane, audio/video switch, ⌘Y preview, recordings from the timestamp, settings window like System Settings | done |
| 10 | GitHub Pages demo: an interactive macOS desktop in the browser that walks through Lantern (plan below) | done: https://misterekkk.github.io/lantern/ (Astro site with docs) |
| 11 | PDFs (PDFKit, page by page; scanned pages via OCR) and text in images (Apple Vision OCR with a cheap gate); exact numbers and codes matched as phrases | done |
| 12 | Spotlight replacement: apps and System Settings, instant answers, dictionary, shortcuts, clipboard, menu items and windows, web row, filter icons, music (Apple Music; opt-in Spotify sync), spelling, learned choices, one-letter search by macOS's launch counts, text on video frames, Whisper noise filter | done (DECISIONS.md, entries of 2026-10-16/17) |
| 13 | Raycast and Alfred as front ends: a Raycast extension (results, previews, thumbnails made by the daemon, the panel's filters and keys) and an Alfred workflow, both clients of the socket; Settings → General → Search from | Raycast done (tested by the user); Alfred untested in Alfred |
| 14 | Self-contained `Lantern.dmg` without developer tools: `AVFoundation` decoding (`lantern-media`, `ffmpeg` optional for MKV/WebM only), SigLIP 2 image tower and EmbeddingGemma 2 processor on MLX without `torch`, first-launch model download in the menu bar and Settings → Performance, `app/build.sh --dmg` and `.github/workflows/release.yml` on `macos-26` | done |
| 15 | 0.1.3, quick wins without a schema change: `in:folder` scoped search, copy the file (⌘C, as Spotlight) or the matched text (⌥⌘C) in the panel and Raycast, a time limit per file in the index worker, pausing on low disk space, the transcription switch documented, `mmap` measured (adopted only if faster) | done (released as 0.1.3; mmap adopted, DECISIONS.md) |
| 16 | 0.2.0 "time and place" (one migration, no re-embedding): `inferred_date` from EXIF / QuickTime / file name / file system, accepted only as a plausible date (from 1990 to today; camera reset dates such as 1970-01-01 and 2000-01-01 00:00 rejected; numbers like `1950` or IDs never read as dates), a PL/EN time parser for queries (a hard filter only for an explicit phrase like "z maja 2023", otherwise a boost), GPS → city and country from an offline GeoNames table behind a switch (CLGeocoder needs the network: rejected), faces only if an evaluation shows a gain | built on branch `phase16-time-place` (DECISIONS.md); faces not done |
| 17 | 0.2.x robustness: pause the worker under macOS memory pressure, careful defaults on 8 GB Macs (optional models off, with a warning), a words-only first phase in Raycast, a permission hint in the app when a folder cannot be read | planned |

### Phase 9 plan (agreed with the user, 2026-10-16)

- **The menu bar stays minimal.** Details live in the settings window (gear).
- **Folder tree with checkboxes:** expands down to single files, three states (checked, unchecked, partial), loads
  on expand, shows the number of indexed files and previews the effect before saving ("N files will leave the
  index"). Always-skipped items are greyed out with the reason. Underneath: the existing path exclusions.
- **Ignored extensions:** pills with ✕, a ＋ button with suggestions that filter as you type (known types +
  extensions present in the folders), any unusual extension allowed. Typing `csv`, `.csv` or `*.csv` and a comma
  (or space, Return) makes a pill. Defaults: `.ipynb`, `.csv`, `.tsv`; `.json` as a suggestion.
- **Performance pane:** live RAM chart (every second while the window is open), daemon and worker separately, the
  footprint Activity Monitor shows. Plus loaded models, current job and speed, session peak and a model list
  (name, revision, size, state).
- **Audio and video switch** (next to photos, frames and transcription).
- **Panel shortcuts (agreed 2026-10-16):** ↩ / double-click = at the match (code and text at the start of the
  passage, recordings from the moment), ⌥↩ / ⌥ double-click = from the start, ⌘Y = preview inside Lantern (text
  passage highlighted or the recording from the moment), ⌘↩ = Finder. Built with ⌘Y instead of Space (Space types
  into the query field; ⌘Y is the system Quick Look shortcut).
- **Recordings from the timestamp:**
  - ⌘Y: plan A, a quick preview with the system `AVPlayerView`, starting ~3 s before the words, with the transcript
    and an "Open in QuickTime Player" button. The system Quick Look has no API for a start time (checked in the SDK).
  - ↩: plan B, QuickTime Player from the moment. QuickTime's scripting dictionary has a writable `current time`
    property; it needs a one-time Automation permission. If plan B fails, plan A opens.
  - ⌥↩: the whole recording from the start in its default app; ⌘↩: Finder. Same for audio and video frames.

### Phase 10 plan: GitHub Pages demo (agreed 2026-10-16)

- A static page that looks like a MacBook desktop (menu bar, wallpaper, Dock) and guides the visitor with Liquid
  Glass hint bubbles: click Lantern in the menu bar → index the new files → close the menu → press ⌥Space (or click
  the highlighted shortcut) → type the suggested phrase → see a photo, a video moment, code at a line, a song.
- **No Apple assets:** no Apple wallpapers, SF fonts, SF Symbols or Apple logo (licences and trademarks). The page
  uses `-apple-system` (Apple visitors see their own system font), its own gradient wallpaper and its own SVG icons.
- **Private like the app:** no analytics, no third-party requests (fonts, CDNs); everything is in the repository.
- Deployed by a GitHub Actions workflow from `site/` once the repository is public; works locally before that.

Out of scope for now: the LightGBM reranker (we only collect `search_events`), LLM folder summaries, sync, cloud,
multiple users.

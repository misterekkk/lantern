# Lantern

Search your Mac **by meaning**, not just by file name: text, code, documents, photos, recordings and videos.
Lantern runs entirely on your Mac. It indexes your folders with Google DeepMind's open **EmbeddingGemma 2** model
(Apache 2.0) on Apple Silicon through MLX, and nothing ever leaves the machine.

- *"decision tree pruning"* finds the README that explains it, even if it is written in Polish.
- *"def add_two(x, y):"* jumps to `script.py`, line 200, in your code editor.
- *"dog on the beach"* finds the photo; *"the cow on the farm"* finds the moment in a recording where it is said.

> **Status:** all planned phases are done (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)). Lantern is a working
> prototype tested on one Mac (M4, 16 GB). This README describes only what exists today.

## Features

- **Spotlight-style search panel** (⌥Space) with sections for code definitions, photos and files; the section that
  fits the query best comes first.
- **Hybrid ranking:** meaning (embeddings) and exact words (SQLite FTS5/BM25) fused with Reciprocal Rank Fusion.
- **Code:** split into functions and classes with tree-sitter (22 languages); a symbol table finds the exact line of
  a definition.
- **Photos and video frames:** two models (EmbeddingGemma 2 vision tower and SigLIP 2) vote on what is in a picture.
- **Recordings and videos:** audio embeddings plus Whisper transcripts, with the moment each phrase is spoken.
- **Menu bar app** with a background daemon, an "ask before indexing" mode, pause on battery and heat, and a
  settings window laid out like System Settings.
- **Private by design:** offline after a one-time model download, read-only access to your files, no telemetry.

## Requirements

| What | Why | Install |
|---|---|---|
| Mac with Apple Silicon (tested: M4, 16 GB) | MLX runs on Apple Silicon only | – |
| Xcode Command Line Tools | compiler, git | `xcode-select --install` |
| Xcode 26 | builds the menu bar app (macOS 26 SDK) | App Store |
| Homebrew | package manager | https://brew.sh |
| uv | Python environment and dependencies | `brew install uv` |
| Python 3.12 | the daemon; **installed by uv**, not the system Python | automatic with `uv sync` |
| ffmpeg | decodes audio and video | `brew install ffmpeg` |

`uv sync` installs everything else into the project's `.venv` (nothing global): MLX and a pinned `mlx-vlm` commit
(EmbeddingGemma 2 support), `transformers`, `torch`, `tokenizers`, `sqlite-vec`, `tree-sitter-language-pack`,
`pathspec`, `charset-normalizer`, `mlx-whisper`, `pillow`, `soundfile`, `watchdog`.

> The python.org Python (e.g. 3.13) does not work: it cannot load SQLite extensions, which `sqlite-vec` needs. The
> Python that uv installs can.

## Installation

```bash
# 1. System tools
xcode-select --install
# Homebrew: https://brew.sh. If `brew` is not on your PATH (Apple Silicon):
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
brew install uv ffmpeg

# 2. Project and dependencies (uv downloads Python 3.12 into ~/.local/share/uv)
cd lantern
uv sync

# 3. One-time model download (needs internet; models go to ~/.cache/huggingface/hub/)
#    a) EmbeddingGemma 2, MLX 8-bit (~1.2 GB) — required
uv run hf download mlx-community/embeddinggemma-2-8bit --revision 7505ef2f8ddef45efef6d060865f27989b3c9cec
#    b) Whisper for transcripts (~1.6 GB) — skip if you do not index recordings or use --no-transcribe
uv run hf download mlx-community/whisper-large-v3-turbo --revision a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb
#    c) SigLIP 2 for photo search (~0.8 GB + ~0.2 GB) — optional; without it EmbeddingGemma 2 ranks photos alone
uv run hf download google/siglip2-base-patch16-224 --revision 75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2
uv run hf download mlx-community/siglip2-base-patch16-224-8bit --revision 5249fc157310584fe99dae6964707278eb6df50f

# 4. Check
uv run lantern --help
uv run pytest

# 5. Menu bar app
app/build.sh --install        # builds, signs and copies Lantern.app to /Applications
open /Applications/Lantern.app
```

Lantern always loads models with `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1`;
an automated test checks that loading and embedding open no network connection.

**Building the app** needs Xcode 26 (the script selects it with `DEVELOPER_DIR`, without changing `xcode-select`).
It signs with your "Apple Development" certificate if you have one (Xcode → Settings → Accounts → Manage
Certificates); otherwise it signs ad hoc and macOS may ask for folder permissions again after every rebuild.

## The app

- **Menu bar icon:** state (ready, indexing, waiting for approval, paused), progress, file count and memory.
  Actions: Index now / Later, Pause / Resume, Add Folder…, Settings…, Quit. A notification with the same actions
  appears when new files are found in "ask" mode.
- **⌥Space** opens the search panel. Results come in sections like in Spotlight: **Definitions** (functions and
  classes, with file, line and signature), **Photos** (with thumbnails) and **Files**. Recordings and videos show the
  moment of the match, e.g. `vlog.mp4 · 1:48`.
- **Shortcuts in the panel** (double-click = ↩, ⌥ + double-click = ⌥↩):

  | Key | Code and text | Recordings and videos |
  |---|---|---|
  | ↩ | open at the match (start of the passage) | QuickTime Player at the moment it is said |
  | ⌥↩ | open from the top | the whole recording from the start, in its default app |
  | ⌘Y | preview in the panel: the passage highlighted, with context | preview in the panel from the moment |
  | ⌘↩ | show in Finder | show in Finder |
  | ↑↓ / Esc | select / close (from a preview: back to the list) | |

  - Files open in the app macOS uses for their type, at the right line when that app supports it: VS Code
    (verified), Cursor, Xcode, JetBrains IDEs. Other apps (TextEdit, …) open the file at the top; the ⌘Y preview
    always shows the passage. Lantern never switches to an app other than your default.
  - The first time a recording opens, macOS asks "Lantern wants to control QuickTime Player". Without that
    permission ↩ shows the preview from the same moment. Formats macOS cannot play (mkv, webm) open from the start
    in their default app (VLC, IINA, …).
  - ⌘Y instead of Space: the panel's focus stays in the query field, where Space types a space. ⌘Y is the Quick
    Look shortcut of Finder.
- **Settings** (Settings… in the menu), laid out like System Settings:
  - **General:** open at login, language, search shortcut, ask before indexing or index automatically,
  - **File Types:** photos, recordings and videos (with transcription and video frames), ignored extensions,
  - **Folders:** indexed folders, *Choose What to Index* (a folder tree with checkboxes, down to single files, with
    name search and the number of indexed files), exclusion patterns,
  - **Battery and Heat:** pause indexing on battery power or when the Mac is hot,
  - **Performance:** live memory chart of the daemon and the indexing process, peaks, current work and speed,
    models (revision, size on disk, where they are loaded),
  - **Privacy:** clear history, compact the index, data folder, macOS permissions; **About**.
- **Language:** the app follows the macOS language (English and Polish today). To choose one for Lantern only:
  Settings → General → Language (Lantern restarts), or System Settings → General → Language & Region → Applications.
- **The app starts the daemon** as its child process, so macOS file permissions (Desktop, Documents, Downloads) are
  granted to Lantern with the usual "Lantern would like to access…" prompts. When the app quits or crashes, the
  daemon stops too. If a daemon from the LaunchAgent is already running, the app uses it.

## Command line

```bash
uv run lantern index ~/Projects            # first run: scan + index; later runs: only changes
uv run lantern search "decision tree pruning"
uv run lantern search "def add_two(x, y):" -n 5
uv run lantern open 1                      # open result 1 of the last search (--reveal: Finder, --print: path:line)
uv run lantern status
uv run lantern remove ~/Projects           # remove a folder and all its data from the index
uv run lantern compact                     # physically erase deleted data from the index file
uv run lantern clear-history               # delete the local search history
```

- `index` compares the folder with the index: new and changed files are indexed, moved files are recognised by
  content (no recomputation), deleted files are removed together with their text and vectors.
- If a folder is unavailable (unplugged disk, network share), nothing is removed. If more than half of a folder's
  files vanished, `index` refuses without `--force`. Files you excluded on purpose (still on the disk) are removed
  without it.
- Every search and opened result is kept in a **local** history (query and result positions, no file content), as
  training data for a future ranking model. `--no-log` skips it, `clear-history` deletes it.
- Changing the chunking rules or the model re-indexes the affected files automatically.

## How search works

- **Two lists, one ranking:** meaning (EmbeddingGemma 2: paraphrases, other languages) and exact words (FTS5/BM25:
  function names, error messages), fused with Reciprocal Rank Fusion. Each result shows where it came from, e.g.
  `(meaning #1 · keywords #3)`. Keyword search skips stop words and matches Polish inflections by stem.
- **Indexed types:** code (Python, JS/TS, Go, Rust, Java, Kotlin, Swift, C/C++, C#, Ruby, PHP and more, split into
  functions and classes), text (Markdown, txt, JSON, YAML, HTML…; text files over 2 MB and binaries are skipped),
  audio (mp3, m4a, wav, flac, ogg, opus, aac, aiff…), video (mp4, mov, mkv, webm, avi…: sound and frames) and photos
  (jpg, png, heic, gif, webp, tiff, bmp).

### Code definitions

- Paste a line of code or just a name: `def add_two(x, y):`, `add_two`, `addTwo`, `class Client`, `Client::connect`,
  `BinMapper.transform`, `public void testReplaceIndex()`. The result is the file, the **exact line** and the
  signature, e.g. `script.py:200   def add_two(x, y)`.
- Names match regardless of style (`add_two` = `addTwo` = `AddTwo`): exactly first, then by prefix (`add_two` →
  `add_two_numbers`) and by fragment (`UserToken` → `parseUserToken`). The number of arguments, the kind (`def`,
  `class`…) and the parent class help pick the right definition. Sentences ("tree pruning") never list definitions.
- Languages: Python, JavaScript, TypeScript, Go, Rust, Java, Kotlin, Swift, C, C++, Objective-C, C#, Scala, Ruby,
  PHP, Lua, R, Dart, Elixir, Haskell, SQL, Bash (each covered by a test). Only definitions are searched, not calls.

### Photos and video frames

- Find a photo by **what is in it** ("dog", "bicycle", "child in the water", "sunset"), in English or Polish, or by
  its file name.
- Two models rate every photo: the EmbeddingGemma 2 vision tower (better at sentences and Polish) and SigLIP 2 base
  (better at single words). Their scores are normalised and summed; a photo is listed only when it clearly stands
  out from the rest.
- Videos: frames at scene changes (ffmpeg), at least one every 30 s, near-duplicates dropped before embedding.
- Skipped: images under 96 px (icons), over 100 MB, or broken. HEIC is decoded with the system's `sips`.
- Speed (M4): ~3 photos/s including model loading, i.e. ~1 hour for 10,000 photos in the background.

### Recordings and videos

- ffmpeg streams the audio (16 kHz mono) window by window, so the length of a recording does not affect memory.
- Two tracks in one ranking: **audio embeddings** of 30 s windows with 5 s overlap (the meaning of what is said,
  also across languages) and **Whisper large-v3-turbo transcripts** in ~20 s chunks (single words, text previews).
- `lantern index <folder> --no-transcribe` skips Whisper (faster, but single words are not found).

## Folders and exclusions

- Add folders in the app (Add Folder…) or with `lantern index <folder>`. You can add your whole home folder:
  `lantern index ~ --dry-run` first counts files and estimates the time without reading or writing anything.
- **Home folder rules** (always on): `~/Library` is skipped (mail, caches, app data) except **iCloud Drive**;
  `~/Applications`, the Apple Music library and all macOS packages (`*.app`, `*.photoslibrary`, iWork documents,
  disk images…) are skipped; hidden folders and files are skipped except `.github`.
- **iCloud:** files that exist only in the cloud are skipped (reading them would download them) and indexed once they
  are on the disk. A file already indexed that iCloud evicts stays in the index.
- **Permissions:** Desktop, Documents, Downloads and iCloud Drive need macOS permission. A folder without access is
  skipped and its previously indexed files are **kept**; `lantern status` shows a warning.
- **Always excluded:** `.git`, `node_modules`, virtual environments, build and cache folders; files with secrets
  (`.env*`, `*.pem`, `*.key`, `*.p12`, SSH keys `id_*`, `.netrc`, credential and token files, `*.tfstate`…);
  lockfiles; everything in `.gitignore` files; symbolic links (Lantern never leaves the folder you chose).
- **Choose what to index:** in Settings → Folders → Choose What to Index, unchecking a file or folder excludes it;
  checking an item inside an excluded folder brings back only that item.
- **Ignored extensions** (default: `.ipynb`, `.csv`, `.tsv` — notebooks and tables are mostly output and numbers):

  ```bash
  uv run lantern ignore list
  uv run lantern ignore add json log        # csv, .csv and *.csv mean the same
  uv run lantern ignore remove csv
  ```

- **Exclusion patterns and paths:**

  ```bash
  uv run lantern exclude add ~/Downloads/some-library   # a folder or file
  uv run lantern exclude add "*.log"                    # a .gitignore-style pattern, in every folder
  uv run lantern exclude list | remove <pattern>
  ```

- **File types:** `lantern config index_photos false`, `index_audio_video false`, `index_video_frames false`,
  `transcribe false` (also in Settings → File Types). Excluded files disappear from the index at the next scan.

## Background daemon

```bash
uv run lantern daemon install     # install and start the LaunchAgent (starts at login, low priority)
uv run lantern daemon run         # or run the daemon in the foreground (Ctrl+C stops it)
uv run lantern pending            # files found by the daemon, waiting for approval
uv run lantern approve | decline  # index them / ask again on the next change
uv run lantern pause | resume
uv run lantern config index_mode auto   # index right away instead of asking (default: ask)
uv run lantern daemon stop | uninstall
```

- **On start** (also after a long shutdown) the daemon scans all folders: deleted files leave the index at once; new
  and changed ones wait for approval in "ask" mode. While running, FSEvents report changes.
- **Indexing runs in a separate background-priority process**: the audio, vision and Whisper models live only there
  and are released when it finishes. The idle daemon uses ~490 MB.
- **Automatic pause** on battery power and on thermal warnings; the current file is finished, the rest waits.
- **Settings** live in `~/Library/Application Support/Lantern/config.toml` (`lantern config` lists them).
- **API:** Unix socket `~/Library/Application Support/Lantern/lantern.sock` (mode `0600`, folder `0700`), JSON lines.
  The menu bar app uses the same API.
- **Logs:** `~/Library/Application Support/Lantern/logs/` (paths and errors, never file contents or queries).

## Privacy

- Everything runs **locally and offline** after the one-time model download. No telemetry, no external APIs.
- Lantern **only reads** the files it indexes. It never modifies, moves or deletes them.
- The index is one SQLite file in `~/Library/Application Support/Lantern/` (not synced with iCloud). It contains text
  passages, code signatures and vectors derived from your files: treat it like a copy of them. Exclude it from
  Time Machine if you like:

  ```bash
  tmutil addexclusion "$HOME/Library/Application Support/Lantern"
  ```

- Deleted means deleted: text, keyword index rows and vectors are removed in one transaction, with SQLite
  `secure_delete`, FTS5 `secure-delete` and `VACUUM` (`lantern compact`, automatic after large removals). A test
  checks that removed text is gone from the raw bytes of the database file.
- **Company code:** nothing leaves the Mac, but check with your IT/security team before indexing company
  repositories. Keep FileVault on and the default secret exclusions in place.
- Details: [docs/PRIVACY.md](docs/PRIVACY.md).

## Known limitations

- The MLX backend uses an **unreleased `mlx-vlm` branch** (pinned commit) and community-converted `mlx-community`
  weights. Vectors match the original model (cosine 0.9998), but this dependency needs watching.
- Memory (measured on M4): idle daemon ~490 MB, plus ~340 MB for the photo query model while the panel is in use
  (released after 5 minutes); the indexing process up to ~2 GB for text, ~3.3 GB for recordings and ~4 GB for photos,
  then it exits.
- Photo search was measured on 1,000 Flickr30k photos and an 80-photo test folder; real libraries (screenshots,
  documents, many similar photos) may behave differently. Text inside images is not read (no OCR).
- Audio search was evaluated on synthetic speech; real recordings (noise, several speakers) may do worse.
  Transcripts cover the first 4 hours of a recording; audio embeddings cover all of it.
- Large files (> 64 MB, mostly videos) are recognised by size and content samples, not a full hash.
- CSV/TSV and large number-heavy files are indexed by their head only (when not ignored).
- Polish inflection in keyword search is approximated by stem prefixes, not a real stemmer.
- Full list of open questions: [docs/DECISIONS.md](docs/DECISIONS.md#otwarte-pytania).

## Development

```bash
uv run pytest            # fast tests (no real model)
uv run pytest -m model   # tests with the real models (downloaded): reference vectors, no network access, search
uv run ruff check
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer swift test --package-path app
```

```
README.md, CLAUDE.md, pyproject.toml
docs/        ARCHITECTURE.md, DECISIONS.md (decisions, measurements, open questions), PRIVACY.md — in Polish
daemon/      the daemon (Python package `lantern`): api, scanner, queue, extractors, chunking, embedding, store,
             search, service, cli.py
app/         the menu bar app (Swift package: LanternCore + Lantern, build.sh)
launchd/     LaunchAgent template
spikes/      measurement scripts behind the decisions (not part of the app)
tests/       pytest suite
```

`CLAUDE.md`, `daemon/CLAUDE.md` and `app/CLAUDE.md` describe the conventions for contributors (and coding agents).

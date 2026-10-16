# Privacy and security

Lantern is meant to work on company code too, so privacy is the overriding requirement.

## Rules

1. **Local and offline.** The only network traffic is the one-time installation: Homebrew packages, Python packages
   (`uv sync`) and the model download (`hf download`), all run by `install.sh` or by hand (README). Afterwards the
   libraries run with `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`,
   `HF_HUB_DISABLE_TELEMETRY=1`, `DO_NOT_TRACK=1`.
2. **Read-only towards your files.** No writes, moves or deletions in indexed folders.
3. **No telemetry** in Lantern's code: no analytics, crash reports or outbound connections.
4. **API** only through a Unix socket with mode `0600`, in a data folder with mode `0700` (tested). No network port.
5. **Secrets excluded by default:** `.env*`, `*.pem`, `*.p12`, `*.pfx`, `*.key`, SSH keys `id_*`, token and credential
   files, plus `.git`, `node_modules`, build and cache folders. `.gitignore` files are respected.
6. **Deleted means deleted:** text, FTS rows and vectors are deleted in one transaction; `PRAGMA secure_delete`,
   FTS5 `secure-delete`, and after deletions `optimize` + `VACUUM` + a WAL checkpoint (`lantern compact`, automatic
   after large deletions and after `lantern remove`). A test checks that deleted text is absent from the raw bytes of
   the database file.
7. **iCloud and permissions:** iCloud files that are not on the disk are never read (reading would download them);
   `~/Library` (mail, app data) is skipped; a folder without macOS permission is skipped without removing its entries.
8. **Photos:** the index holds only vectors of photos and frames (no thumbnails or copies). Thumbnails in the panel
   are generated on the fly by macOS (Quick Look) from the original files. Video frames go to a temporary folder
   during indexing and are deleted as soon as they are read into memory (before embedding).
9. **Settings and preview:** the folder tree and its search read only file and folder names (never contents, never
   iCloud-only folders). The ⌘Y preview reads only files that are already in the index. Opening a recording in
   QuickTime is a local script (Apple Events) with macOS permission; nothing leaves the Mac. The Performance pane shows
   only numbers (memory, progress) and the path of the current file.
10. **Text in images and PDFs:** text is read on the Mac by macOS itself (Vision, PDFKit) through PyObjC; a test
    checks that reading opens no network connection. The recognised text is stored in the index like document text,
    so private text in screenshots (passwords, banking) becomes searchable: OCR can be switched off in Settings → File
    Types. PDFs are only opened for reading, never saved.
11. **Search history** (`search_events`) is local only: queries and result positions, no file contents.
    `lantern search --no-log` skips it, `lantern clear-history` deletes it and compacts the database.

## Where the data is

| Data | Location | Notes |
|---|---|---|
| Index (SQLite) | `~/Library/Application Support/Lantern/index.db` | not synced with iCloud; exclude it from Time Machine (README) |
| Settings | `~/Library/Application Support/Lantern/config.toml` | `lantern config` |
| API socket | `~/Library/Application Support/Lantern/lantern.sock` | `0600`, only while the daemon runs |
| Logs | `~/Library/Application Support/Lantern/logs/` | file paths and error messages; no file contents or queries |
| Models | `~/.cache/huggingface/hub/` | public weights, no user data |

The index holds **derived data**: text passages, names and signatures of code definitions, and vectors from which
content can partly be reconstructed. `search_events` holds the query history. Treat the index file like a copy of
the indexed data.

## What the libraries do (measured)

| Library | Network behaviour | How it is contained |
|---|---|---|
| huggingface_hub / transformers | without offline mode: requests to huggingface.co when loading a model (version checks) | offline variables; **measured: 0 connection attempts** while loading and embedding text and audio |
| sentence-transformers (fallback backend) | as above | as above |
| MLX / mlx-vlm | no traffic of their own; weights through huggingface_hub | `local_files_only`; **measured: 0 connection attempts** (`-m model` test) |
| mlx-whisper | weights through huggingface_hub | loaded from the local copy (`local_files_only`); **measured: 0 connection attempts** while transcribing (`-m model` test) |
| SigLIP 2 (transformers + torch in the worker, MLX in the daemon) | weights through huggingface_hub | `local_files_only` + offline variables; **measured: 0 connection attempts** while loading and embedding photos and queries (`-m model` test) |
| Pillow, `sips` (photos) | no network traffic; read the photo file | HEIC through `sips` into a temporary file that is deleted right away |
| ffmpeg | no network traffic; reads the input file, output goes to a pipe | run with `-nostdin`, no output files |
| Ollama (**not used**) | its server periodically fetches "model recommendations" from ollama.com (visible in its log) | not part of Lantern |
| Homebrew (installation) | Homebrew analytics during `brew install` | `install.sh` runs brew with `HOMEBREW_NO_ANALYTICS=1`; for your own brew calls: `brew analytics off` |

Measurement: `spikes/phase0/probe_network.py` intercepts `socket.connect` and `getaddrinfo` in the Python process;
the `-m model` tests do the same for every model Lantern loads.

## The demo website (GitHub Pages)

The demo page in `site/` loads nothing from third parties (no analytics, web fonts or CDNs) and sends nothing
anywhere; it only plays a scripted walkthrough with sample data stored in the repository.

## Company code

- Agree on the use with IT/security before indexing company repositories.
- Turn on FileVault, exclude the index from Time Machine, keep the default secret exclusions.
- Removing a folder removes its data from the index, and `VACUUM` erases it from the file.

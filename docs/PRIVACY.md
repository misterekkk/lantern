# Privacy and security

Lantern is meant to work on company code too, so privacy is the overriding requirement.

## Rules

1. **Local and offline.** The only network traffic is the one-time installation and model download:
   - the prebuilt app (`Lantern.dmg`): the models are downloaded from Hugging Face (huggingface.co) only when you click
     **Download** in the menu bar or in Settings → Performance. The daemon then runs `python -m lantern.models` as a
     separate process without the offline switches (`HF_HUB_DISABLE_TELEMETRY=1` and `DO_NOT_TRACK=1` stay set),
     fetches the pinned revisions into `~/.cache/huggingface/hub/` and loads them offline;
   - building from source: Homebrew packages, Python packages (`uv sync`) and the model download (`hf download`), run by
     `install.sh` or by hand (README).

   Everything else runs with `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1`,
   `DO_NOT_TRACK=1`. The one other connection is the opt-in Spotify sync (rule 15).
2. **Read-only towards your files.** No writes, moves or deletions in indexed folders.
3. **No telemetry** in Lantern's code: no analytics, crash reports or outbound connections. The one connection Lantern
   itself can make is the opt-in Spotify sync below, which you switch on and sign in to yourself.
4. **API** only through a Unix socket with mode `0600`, in a data folder with mode `0700` (tested). No network port.
5. **Secrets excluded by default:** `.env*`, `*.pem`, `*.p12`, `*.pfx`, `*.key`, SSH keys `id_*`, token and credential
   files, plus `.git`, `node_modules`, build and cache folders. `.gitignore` files are respected (also those
   above the indexed folder inside the same repository).
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
10. **Text in images, PDFs and video frames:** text is read on the Mac by macOS itself (Vision, PDFKit) through PyObjC; a test
    checks that reading opens no network connection. The recognised text is stored in the index like document text,
    so private text in screenshots (passwords, banking) becomes searchable: OCR can be switched off in Settings → File
    Types. PDFs are only opened for reading, never saved.
11. **Applications and System Settings:** only the bundles' own descriptions are read (Info.plist, translations,
    keywords, the search terms of System Settings), plus the launch count and last-use date macOS's Spotlight keeps
    for each app on the Mac (kept in memory, used to rank apps); nothing is launched, and opening a pane uses macOS's
    x-apple.systempreferences links. The ⌘Space option reads whether Spotlight's shortcut is on; it never writes
    system settings.
12. **Clipboard history** (off by default; Settings → Privacy: 30 minutes or 8 hours): copied text is kept in the
    app's memory only, never on disk, in the index or in the search history; items that apps mark as private
    (nspasteboard.org concealed / transient types, used by password managers) are skipped; it is forgotten after
    the chosen time, when switched off, or when Lantern quits.
13. **Menu items and windows** (off by default; Settings → Privacy): with the Accessibility permission, Lantern reads
    the menu titles of the app in front and the titles of open windows when the panel opens, keeps them only while
    it is open, and presses the item or raises the window you choose. Nothing is stored or logged.
14. **Apple Music** (off by default; Settings → Privacy): the app reads song titles, artists, albums, track numbers
    and playlists through Apple's iTunesLibrary framework (macOS asks for Media & Apple Music) and keeps them in the
    local index; the account name or e-mail stored in the downloaded files is never read. Songs play through the Music
    app (AppleScript; macOS asks once). Switching it off deletes the list from the index.
15. **Spotify** (off by default; Settings → Privacy): Spotify keeps no readable list of your music on the Mac (its
    downloads are an encrypted cache, never read), so the app fetches your playlists and liked songs from Spotify's
    Web API with **your own** app key (Client ID; sign-in with PKCE in your browser, Lantern never sees the password).
    Connections: accounts.spotify.com (sign-in, token refresh) and api.spotify.com (the library), only at sign-in,
    once a day and on "Sync Now"; during sign-in a listener waits on 127.0.0.1 (never other interfaces) for the one
    redirect back. Read: playlist names, song titles, artists, albums. The refresh token and the Client ID are kept in
    your login keychain. Searching and playing work offline from the local copy. Switching it off or signing out deletes the list.
    To play Liked Songs (which have no playlist address), Lantern reads one value from the Spotify app's local prefs
    file, the signed-in account name (`autologin.canonical_username`), when you play them; it is not stored or sent.
16. **Spelling:** guesses come from macOS's spell checker (NSSpellChecker, on the Mac); Lantern never teaches it
    words and sends nothing out. The daemon only compares the guesses with the index.
17. **Search history** (`search_events`) is local only: queries, result positions and the row you
    opened, no file contents. It teaches Lantern your usual choice for a query ("sl" → Slack); Settings → Privacy →
    Keep search history switches it off. "Choices learned by Alfred · Import" (shown only when Alfred's
    database exists) reads that file once, read-only, and adds its app choices to this history.
    `lantern search --no-log` skips it, `lantern clear-history` deletes it and compacts the database.
18. **Raycast and Alfred** (integrations/, off unless you choose them in Settings → General): the extension or
    workflow sends your query to Lantern's local socket (user-only, mode `0600`) and shows the results; nothing goes
    over the network. The query and the results pass through that launcher's own process, so its privacy policy
    applies to them (Raycast has accounts and cloud features; Alfred is local). Raycast cannot read your folders
    (macOS permissions belong to Lantern), so for photo and video results the daemon writes a small JPEG thumbnail
    (a video's frame at the result's moment) to `~/Library/Caches/Lantern/thumbnails` (mode `0700`, files `0600`),
    only when Raycast asks and only for indexed files; each is deleted after 10 minutes, and all of them when the
    daemon starts. This is the one place Lantern writes a picture of your files to disk.

19. **Time and place** (phase 16): Lantern reads the date and the GPS location saved in photos and videos (EXIF,
    QuickTime) and dates in file names. The location becomes a city through a table of GeoNames cities (CC-BY 4.0)
    shipped inside Lantern, so no coordinates are sent anywhere (Apple's geocoder was rejected because it sends them
    to Apple). The index keeps the coordinates, the city (or a country named by a camera photo's folder) and only the cities your
    files were made in. Switching
    `places` off (Settings → File Types) deletes every coordinate and city from the index; a city whose last photo
    is deleted is removed with it. Dates stay. Reading them opens only file headers, never iCloud-only files.

20. **People in photos** (phase 16): macOS's Vision framework counts the faces and bodies in each photo, on the Mac,
    and the photo model (already on the Mac) guesses from appearance whether each of the four largest people looks
    like a woman or a man. The index stores only the numbers (how many people, how many women and men, the size of
    the largest face), never face images, face prints or who anyone is. Switching `people` off (Settings → File
    Types) deletes every count.

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
| SigLIP 2 (MLX: fp16 image tower in the worker, 8-bit text tower in the daemon) | weights through huggingface_hub | `local_files_only` + offline variables; **measured: 0 connection attempts** while loading and embedding photos and queries (`-m model` test) |
| Pillow, `sips` (photos) | no network traffic; read the photo file | HEIC through `sips` into a temporary file that is deleted right away |
| lantern-media (AVFoundation, part of macOS) | no network traffic; reads the input file, audio goes to a pipe, key frames to a temporary folder | frames deleted after reading, like ffmpeg's |
| ffmpeg (optional, MKV/WebM) | no network traffic; reads the input file, output goes to a pipe | run with `-nostdin`, no output files |
| Ollama (**not used**) | its server periodically fetches "model recommendations" from ollama.com (visible in its log) | not part of Lantern |
| Homebrew (installation) | Homebrew analytics during `brew install` | `install.sh` runs brew with `HOMEBREW_NO_ANALYTICS=1`; for your own brew calls: `brew analytics off` |

Measurement: `spikes/phase0/probe_network.py` intercepts `socket.connect` and `getaddrinfo` in the Python process;
the `-m model` tests do the same for every model Lantern loads.

## The website (GitHub Pages)

The website in `site/` (landing page, docs, demo) loads nothing from third parties: no analytics, no CDNs, fonts
bundled with the site. It sends nothing anywhere; the demo only plays a scripted walkthrough with sample data stored
in the repository. Astro's build telemetry is switched off in the build scripts.

## Company code

- Agree on the use with IT/security before indexing company repositories.
- Turn on FileVault, exclude the index from Time Machine, keep the default secret exclusions.
- Removing a folder removes its data from the index, and `VACUUM` erases it from the file.

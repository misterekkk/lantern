# Command line reference

`lantern` is the command-line client of Lantern. Everything the menu bar app does is available here, plus a few
things it does not show (dry runs, raw history access through `open --print`, the socket API). This page is the
technical reference; the README has the short tour.

```
lantern [-h] [--version] <command> [options]
```

`./install.sh` links the command to `~/.local/bin/lantern` and this manual's short form to `man lantern`
(`docs/lantern.1`); without it, run `uv run lantern …` from the repository folder or call `.venv/bin/lantern`
directly. Every command has `--help`.

## How commands run

Most commands work in two ways:

- **With the daemon running** (started by the app, by `lantern daemon run`, or by the LaunchAgent), the command is a
  request over the daemon's Unix socket. The model is already in memory: a search takes tens of milliseconds, and
  indexing continues in the background (`lantern status` shows progress).
- **Without the daemon**, the command opens the index itself. `search` then loads the model first (a few seconds),
  and `index` indexes in the foreground with a progress line.

| Command | Without the daemon | Notes |
|---|---|---|
| `index`, `search`, `remove`, `compact`, `clear-history`, `exclude`, `ignore`, `config`, `open`, `status` | works | `status` shows a shorter, local report |
| `pending`, `approve`, `decline`, `pause`, `resume` | error, exit 1 | they control the daemon |

Never run a second indexing process next to a daemon that indexes: let the daemon do it (`lantern index` sends the
request to it automatically).

## Indexing

### `lantern index <path> [--force] [--no-transcribe] [--dry-run]`

Adds a folder to the index or brings it up to date. Lantern only reads files; it never writes, moves or deletes them.

- New and changed files are indexed (change = size or modification time differs, then the content hash is checked);
  moved files are recognised by content and keep their vectors; deleted files are removed with their text and
  vectors in one transaction.
- If the folder is unavailable (unplugged disk, network share), nothing is removed. If more than half of the folder's
  indexed files are gone from the disk, the command refuses (exit 1) unless `--force` is given. Files you excluded on
  purpose do not count.
- `--dry-run` reads only file names and sizes: per-kind counts, sizes, folders without access and a rough estimate
  of the first indexing time. Nothing is read or written.
- `--no-transcribe` (only without the daemon) indexes audio and video by sound, without Whisper transcripts.

Output without the daemon:

```
scan /Users/me/Projects: 42 new, 3 changed, 1 moved, 0 removed, 610 unchanged (0.4s)
  indexing 12/45 notes.md                       (progress on stderr, only in a terminal)
indexed 45 files (312 chunks) in 18.4s, skipped 0, failed 0
```

Exit codes: 0 done, 1 refused or folder unavailable, 3 finished but some files failed (listed on stderr; they are
retried on the next run).

### `lantern remove <path>`

Removes an indexed folder and everything derived from it (text passages, keyword index, vectors, symbols), then
compacts the database so the data is physically erased from the file.

### `lantern compact`

`VACUUM` + FTS `optimize` + WAL checkpoint. Run automatically after large deletions; prints the size before and
after.

## Searching

### `lantern search "<query>" [-n N] [--only KIND] [--no-log]`

One ranking from meaning (EmbeddingGemma 2) and exact words (FTS5/BM25), fused with Reciprocal Rank Fusion; code
definitions and photos come in their own sections, in the order the daemon decides.

| Option | Meaning |
|---|---|
| `-n`, `--limit N` | number of results in the files section (default 10) |
| `--only KIND` | `apps`, `settings` (System Settings), `shortcuts`, `code`, `docs`, `pdf`, `photos`, `media` (recordings and videos) or `music` (Apple Music songs and playlists, sent by the app): the panel's filter pills |
| `--no-log` | do not record this search in the local history |

`in:<folder>` anywhere in the query keeps results to files under folders whose name starts with that word
(`lantern search "in:down umowa"`), `in:"two words"` for a name with spaces, or `in:~/path` / `in:/path` for
everything under a path. Apps, System Settings, Shortcuts, music and answers are left out of a scoped search.

Output:

```
5 results (38 ms)
Definitions:
   1. /Users/me/calc/math_utils.py:200   def add_two(x, y)   (function, exact)
Files:
   2. /Users/me/calc/calculator.py:41-48  [total]   (meaning #1 · keywords #2)
      result = add_two(subtotal, tax)
   3. /Users/me/Bills/invoices_2025.pdf  (page 10)   (meaning #2)
   4. /Users/me/Movies/vlog.mp4 @ 1:48–2:13   (meaning #3 · keywords #1)
Photos:
   5. /Users/me/Desktop/Screenshot 2025-10-14.png   (image #1 · keywords #1)
open a result with: lantern open <number>
```

- Locations: `path:start-end [symbol]` for text and code, `path (page N)` for PDFs, `path @ m:ss–m:ss` for
  recordings, `path:line` with the signature for definitions.
- The sources in brackets show why a result matched: `meaning #k`, `keywords #k`, `image #k` (rank in that list).
- Queries are folded like the index: case, accents and letters such as ł, ø, ß are ignored (`lodz` finds `Łódź`).
  Numbers and codes with separators (`187,15`, `FV/2025/10/1187`) must match as a whole.

### `lantern open <n> [--reveal | --print]`

Opens result `n` of the last search with its default app and records the choice in the local history (training
data for a future ranking model; nothing leaves the Mac).

- `--reveal` shows it in Finder.
- `--print` only prints `path[:line][ @ m:ss]`, for scripts and editors:

```bash
lantern search "retry with backoff" --no-log >/dev/null && code -g "$(lantern open 1 --print)"
```

### `lantern clear-history`

Deletes the local search history (`search_events`) and compacts the database.

## Background daemon

### `lantern daemon run | install [--print] | uninstall | stop`

| Action | Effect |
|---|---|
| `run` | runs the daemon in the foreground (Ctrl+C or `daemon stop` ends it) |
| `install` | writes `~/Library/LaunchAgents/com.lantern.daemon.plist` and starts it (at every login from now on) |
| `install --print` | prints that plist and changes nothing |
| `uninstall` | stops and removes the LaunchAgent |
| `stop` | asks the daemon to stop; the current file is finished first |

The menu bar app starts its own daemon as a child process, so the LaunchAgent is optional. If one is already running,
the app uses it.

### `lantern status`

With the daemon: process, memory, mode, pause reason, power (source, Low Power Mode, thermal state), current work,
queue, files per state, roots and warnings. Without it: the index file, its free space, roots, files, jobs, the model
and the history size.

### `lantern pending | approve | decline | pause | resume`

In "ask" mode (the default) the daemon finds new and changed files but indexes nothing until you approve.
`pending` lists what waits, `approve` indexes it, `decline` leaves it until the next change. `pause` / `resume` stop
and restart background indexing (search keeps working).

## Settings

### `lantern config [key [value]]`

Without arguments, lists every setting. Stored in `~/Library/Application Support/Lantern/config.toml`; a running
daemon applies changes at once.

| Key | Default | Meaning |
|---|---|---|
| `index_mode` | `ask` | `ask`: new files wait for approval; `auto`: index right away |
| `pause_on_battery` | `true` | pause indexing on battery power |
| `pause_on_low_power` | `true` | pause indexing in Low Power Mode |
| `pause_on_thermal` | `true` | pause when macOS reports the Mac is hot (thermal state serious or worse) |
| `index_photos` | `true` | index photos (two image models) |
| `index_image_text` | `true` | read text in photos and screenshots (Apple Vision, on device) |
| `places` | `true` | GPS of photos and videos -> city (built-in table, offline); `false` deletes saved locations |
| `index_audio_video` | `true` | index audio files and videos |
| `transcribe` | `true` | Whisper transcripts of recordings (off: see *Transcription on and off* in README) |
| `index_video_frames` | `true` | index frames of videos as photos |
| `notify` | `true` | macOS notifications (new files found, indexing done) |
| `debounce_s` | `3.0` | seconds of quiet after file changes before a rescan |

Booleans accept `true/false`, `1/0`, `yes/no`. The two lists have their own commands:

### `lantern exclude add|remove|list [pattern]`

Extra exclusions on top of the defaults (`.git`, `node_modules`, build and cache folders, secrets such as `.env*`,
`*.pem`, `id_*`) and `.gitignore`. A pattern is an absolute path (`~` allowed) or a `.gitignore`-style pattern
matched inside every indexed folder (`*.log`, `build/`). Files that are already indexed leave the index at once
with the daemon running, otherwise at the next `lantern index`.

### `lantern ignore add|remove|list [extensions]`

File extensions that are never indexed (default `.ipynb .csv .tsv`). `csv`, `.csv` and `*.csv` mean the same;
several can be given at once (`lantern ignore add log tmp`).

## Environment

| Variable | Effect |
|---|---|
| `LANTERN_HOME` | data folder instead of `~/Library/Application Support/Lantern` (index, settings, socket, logs) |
| `LANTERN_SOCKET` | socket path instead of `$LANTERN_HOME/lantern.sock` (macOS limits socket paths to 104 bytes) |
| `LANTERN_MEDIA` | path to `lantern-media` (AVFoundation decoder; set by the app, otherwise `/Applications/Lantern.app` or `app/build`; `off` forces `ffmpeg`) |
| `LANTERN_FFMPEG` | path to `ffmpeg` for MKV/WebM fallback (otherwise `PATH`, then the Homebrew locations) |
| `LANTERN_NOTIFY=0` | no notifications, whatever the setting says |
| `LANTERN_WORKER_PRIORITY` | `background` (default) runs the indexing process with the background policy; any other value keeps normal priority |
| `LANTERN_EMBEDDER=fake` | deterministic fake models: for tests and trying the CLI without downloading models |

An isolated instance for experiments, with no models and no effect on your real index:

```bash
export LANTERN_HOME=/tmp/lantern-try LANTERN_SOCKET=/tmp/lantern-try.sock LANTERN_EMBEDDER=fake
lantern daemon run &
lantern index ~/some/folder && lantern search "anything"
lantern daemon stop
```

Lantern always runs the model libraries offline (`HF_HUB_OFFLINE=1` and the related variables are set by the code);
there is nothing to configure for privacy.

## Socket API

The daemon speaks newline-delimited JSON over its Unix socket (mode `0600`, folder `0700`): one request per
connection, `{"id", "method", "params"}` in, `{"id", "result"}` or `{"id", "error": {"type", "message"}}` out.
The CLI and the app use only this API, so anything they do can be scripted:

```bash
SOCK="$HOME/Library/Application Support/Lantern/lantern.sock"
printf '{"id": 1, "method": "search", "params": {"query": "invoice october", "limit": 5}}\n' | nc -U "$SOCK"
```

Methods include `ping`, `status`, `search` (`query`, `limit`, `log`, `category`), `index` (`path`, `force`),
`remove` (`path`), `pending`, `approve`, `decline`, `pause`, `resume`, `config_get`, `config_set` (`key`, `value`),
`exclude` (`action`, `pattern`), `extensions` (`action`, `extension`), `compact`, `clear_history`, `preview`, `perf`,
`download_models` (`models`) and `shutdown`. They are the `api_<method>` functions in
`daemon/lantern/service/core.py`; the API is internal and may change between versions.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success (also "nothing waiting", "daemon is not running" for `daemon stop`) |
| 1 | error: daemon needed but not running, folder unavailable, deletion guard, unknown setting, no such result |
| 2 | invalid arguments (from argparse) |
| 3 | `index` finished, but some files failed |

## Files

| Path | Contents |
|---|---|
| `~/Library/Application Support/Lantern/index.db` | the index (SQLite + FTS5 + sqlite-vec); treat it like a copy of the indexed data |
| `~/Library/Application Support/Lantern/config.toml` | settings |
| `~/Library/Application Support/Lantern/lantern.sock` | the API socket, only while the daemon runs |
| `~/Library/Application Support/Lantern/logs/` | daemon logs: paths and errors, never file contents or queries |
| `~/.cache/huggingface/hub/` | downloaded models |

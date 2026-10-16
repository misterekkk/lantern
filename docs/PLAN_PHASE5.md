# Phase 5 plan: background daemon and API

**Status:** done (2026-10-15); results in DECISIONS.md, section "Phase 5: results".

## Goal

Lantern runs in the background as a LaunchAgent. The model is always ready (fast search for a future Spotlight-like
panel), folder changes are detected automatically, and in "ask" mode the daemon indexes nothing without approval. The
CLI and the future Swift app are only API clients.

## Parts

| Part | Solution |
|---|---|
| Process | `lantern daemon run` (foreground; launchd starts it this way) |
| API | Unix socket `~/Library/Application Support/Lantern/lantern.sock`, mode `0600`, folder `0700`; protocol: JSON lines (`{"id", "method", "params"}` → `{"id", "result"}` / `{"id", "error"}`), easy to use from Python and Swift |
| Methods | `status`, `search`, `opened`, `roots.list/add/remove`, `scan`, `pending`, `approve`, `decline`, `pause`, `resume`, `config.get/set`, `shutdown` |
| Start | scan all folders (source of truth, also after a long shutdown); an unavailable folder is skipped without deletions |
| Indexing mode | `ask` (default): new and changed files wait for `approve` (CLI `lantern approve`, later a menu bar button and a notification); `auto`: index right away. Deletions apply immediately (privacy) |
| Watcher | FSEvents (`watchdog`), events grouped (debounce of a few seconds), then a scan of the changed folder; the scan at start stays the source of truth |
| Background work | one indexing thread with background QoS, search on a separate normal-priority thread; the model guarded by a lock (later: a separate worker process, see the Phase 5 results) |
| Pause | manual (`pause`/`resume`), automatic on battery power and on thermal warnings (`pmset -g batt`, `pmset -g therm`); stops between files, unfinished jobs go back to the queue |
| Settings | `config.toml` in the data folder: `index_mode`, `pause_on_battery`, `transcribe`, `notify`, extra exclusions |
| LaunchAgent | `lantern daemon install` / `uninstall` (plist from a template, `launchctl bootstrap gui/<uid>`); installed by the user |
| CLI | `search`, `status`, `pending`, `approve`, `pause`, `resume` talk to the daemon when it runs; without it they work directly on the database as before |
| Idle memory | try to go from ~790 MB down to ~450 MB by loading the model without importing torch (open question 13) |

## Tests

API over the socket (fake embedder, short socket path in `/tmp`), mode `0600`, `ask` mode (pending → approve →
indexed), `auto` mode, a new file detected by the watcher, pause and resume during the queue (jobs go back to the
queue), pause on battery (`pmset` parser), `shutdown`.

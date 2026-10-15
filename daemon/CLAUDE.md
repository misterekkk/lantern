# Daemon (`daemon/lantern`)

All indexing and search logic lives here. Clients (CLI, Swift app) never touch the DB directly.

## Commands

```bash
uv run lantern index <path>     # scan + index changes (--force to allow purging > 50% of a root)
uv run lantern search "<query>" # fused vector + keyword ranking (--no-log skips history)
uv run lantern open <n>         # open result n of the last search, records the choice
uv run lantern remove <path> | compact | clear-history | status
LANTERN_HOME=/tmp/x LANTERN_EMBEDDER=fake uv run lantern index <path>   # isolated DB, no model
uv run pytest tests/            # from repo root
```

## Database

Schema: `lantern/store/schema.sql` (version in `PRAGMA user_version`). On change: bump `SCHEMA_VERSION`, update
schema.sql for fresh DBs and add the upgrade statements to `_MIGRATIONS` in `store/db.py`. Single SQLite file, WAL mode, `sqlite-vec` loaded per connection.

- `roots` → `files` → `chunks` (ON DELETE CASCADE). `chunks_fts` (FTS5) and `vec_chunks_<model_id>` (vec0) are
  virtual tables without FK support: **delete their rows explicitly in the same transaction** as the chunks.
- `embedding_models`: one row per (name, revision, backend, dtype, dim). One vec0 table per model. Never query
  across models; switching model means reindexing into a new table.
- Change detection: (path, size, mtime_ns) first; `content_hash` only when those differ or for move detection.
- Before purging missing files, check that the root exists and is readable. If not, skip the purge.

## Job queue rules

- Enqueue is an upsert on `(file_id, kind)`: re-enqueueing the same work is a no-op.
- Every job must be safe to re-run from scratch (delete the file's old chunks, then insert new ones, one transaction).
- On startup, `running` jobs from a crashed run go back to `queued`.
- A failing file sets `failed`, `last_error` and `attempts += 1`, then the queue moves on. Retry up to a limit.

## Pipeline (`lantern/indexer.py`)

scan (`scanner/diff.py`) enqueues jobs → `run_queue` stage 1 (`index` jobs): text/code files are extracted + chunked,
their chunks embedded together, one transaction per file; audio/video files are streamed by ffmpeg into 30 s windows
(25 s hop) and embedded by the audio tower, then a `transcribe` job is queued → stage 2 (`transcribe` jobs): Whisper →
~20 s transcript chunks → text embeddings + FTS. The audio tower and Whisper are never loaded at the same time;
`replace_file_chunks(kinds=...)` lets each stage replace only its own chunk kinds. Data-like files (CSV/TSV, mostly numbers) are indexed head-only;
lockfiles are excluded (`config.py`). **Bump the kind's entry in `PIPELINE_VERSIONS` (`indexer.py`) whenever
its chunking rules or prompt formats change**: files store `pipeline_version` and `model_id`, and `run_queue` re-queues mismatches automatically.

## Search (`lantern/search/`)

`basic.py` builds two file-level candidate lists (vector KNN, FTS5 BM25 with column weights); `hybrid.py` fuses them
with RRF (k = 60) and logs every search to `search_events` (features only, never file content).

## Service (`lantern/service/`, Phase 5)

- `core.py` — `Daemon`: API server (Unix socket, JSON lines, `api_<method>` handlers), control loop (watcher scans,
  power policy, worker start/stop), ask/auto approval. The text model lives on **one dedicated thread**
  (`model_thread`): MLX GPU streams are per-thread, so never call the embedder from API threads directly.
- `worker.py` — index worker process (`python -m lantern.service.worker`): background priority, drains the queue,
  exits; SIGTERM = finish current file, return the rest to the queue. Heavy models live only here.
- `watcher.py` (FSEvents via watchdog, debounce), `power.py` (`pmset` parsers), `settings.py` (`config.toml`),
  `notify.py` (osascript), `launchagent.py` (plist via plistlib, `launchctl bootstrap gui/<uid>`).
- Adding an API method: `def api_<name>(self, **params) -> dict` in `Daemon` + CLI client code + a test in
  `tests/test_daemon.py` (real socket in `/tmp`; macOS limits socket paths to 104 bytes).
- `embedding/mlx_loader.py` loads EmbeddingGemma 2 without importing torch (package stubs). If a new mlx-vlm version
  pulls transformers again, check with `python -X importtime` / the stub list.

## Adding an extractor

1. Add a module in `lantern/extractors/` that turns a path into text (or audio samples) without modifying the file.
2. Register file extensions/kinds. Respect size limits and skip binaries.
3. Add a chunker in `lantern/chunking/` if the generic one does not fit. Chunks carry path/symbol or timestamps.
4. Tests with small fixture files in `tests/`.

## Adding an embedding backend

1. Implement the `Embedder` protocol (`lantern/embedding/base.py`): `embed_text(texts)` on already-formatted prompts
   (`format_query` / `format_document`), returning L2-normalised float32, and `model_info()`. `embed_audio` comes in Phase 4.
2. `model_info()` must return name, revision, backend, dtype, dim. A different value means a different model_id.
3. Add a `model`-marked test comparing vectors against the reference backend (cos ≥ 0.999 for the same weights precision).
4. Load lazily. Unload the audio tower when no audio jobs are queued (RAM budget, 16 GB machine).
5. MLX backends must set `mx.set_memory_limit` / `mx.set_cache_limit` (without them indexing reached 13 GB).

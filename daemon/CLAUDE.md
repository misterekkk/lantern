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
- `scanner/walk.py` reports unreadable folders and iCloud-only (`SF_DATALESS`) items in `WalkReport`; their indexed
  files are kept (`report.protects`). Never read a dataless file (it would download it). Home-folder policy, bundles,
  hidden folders and user excludes (`settings.exclude`) are applied there too.

## Time and place (Phase 16)

- `files.inferred_date` (wall-clock seconds), `date_source`, `lat`/`lon`, `place_id`, `country` (the city's, or a
  camera photo's folder: `places.country_in_path`), `meta_version`: filled by
  `filemeta.refresh` (daemon control loop, `lantern index`) from file headers (`extractors/metadata.py`), never by the
  worker. A changed or moved file gets `meta_version = 0`; bump `filemeta.META_VERSION` when the cascade changes
  (headers are read again, nothing is re-embedded). `places`/`place_names` hold only cities of indexed files;
  `delete_files` and `forget_places` remove unused ones.
- `search/when.py` (time phrases) and `places.find_in_query` feed `hybrid.parse_filters`, which builds one
  `search.scope.Scope` (folder + time + place) used by every candidate list.

## Job queue rules

- Enqueue is an upsert on `(file_id, kind)`: re-enqueueing the same work is a no-op.
- Every job must be safe to re-run from scratch (delete the file's old chunks, then insert new ones, one transaction).
- On startup, `running` jobs from a crashed run go back to `queued`.
- A failing file sets `failed`, `last_error` and `attempts += 1`, then the queue moves on. Retry up to a limit.

## Pipeline (`lantern/indexer.py`)

scan (`scanner/diff.py`) enqueues jobs → `run_queue` stage 1 (`index` jobs): text/code files are extracted + chunked,
their chunks embedded together, one transaction per file; audio/video files are streamed by `lantern-media`
(AVFoundation; ffmpeg fallback for MKV/WebM, `media._decoder`) into 30 s windows (25 s hop) and embedded by the audio tower, then a `transcribe` job is queued → stage 2 (`transcribe` jobs): Whisper →
~20 s transcript chunks → text embeddings + FTS. The audio tower and Whisper are never loaded at the same time;
`replace_file_chunks(kinds=...)` lets each stage replace only its own chunk kinds. Data-like files (CSV/TSV, mostly numbers) are indexed head-only;
lockfiles are excluded (`config.py`). **Bump the kind's entry in `PIPELINE_VERSIONS` (`indexer.py`) whenever
its chunking rules or prompt formats change**: files store `pipeline_version` and `model_id`, and `run_queue` re-queues mismatches automatically.

- Media decoding: `find_media_helper()` (LANTERN_MEDIA from the app, `off` forces ffmpeg; tests run both ways with
  `LANTERN_MEDIA=off uv run pytest`). Never bump a pipeline version for a decoder change unless vectors change.

## Search (`lantern/search/`)

`basic.py` builds file-level candidate lists: vector KNN, FTS5 BM25 with column weights, and photos
(`image_search`: KNN pool from both photo models, exact similarities, per-model z-scores summed, only z ≥ 1 per model).
`spelling.py` applies the app's spell-checker guesses only when the index knows the guess and not the typed word.
`hybrid.py` puts code definitions (`search/symbols.py`) first, fuses vector + keyword with RRF (k = 60) into the files section; photos (image kind, plus photos matched by
name) form a separate section, and `photos_first` compares list confidences. Video frames stay in the files list
with their video. Every search is logged to `search_events` (features only, never file content).

## Code symbols (Phase 8)

- `chunking/symbols.py` extracts every definition (functions, methods, classes, types, SQL tables) with its exact
  line, kind, signature and parent. Grammar quirks live in `definition_name` / `_extra_definition` (C/C++ declarators
  — only a real `function_declarator` counts, macros parse as fake functions —, Go type specs, R assignments, Elixir
  `def` calls, JS arrow functions). The chunker reuses the same name rules.
- Stored in the `symbols` table (regular table, CASCADE with the file; `name_key` = style-insensitive name), written
  in the same transaction as the code chunks. Changing extraction rules = bump `PIPELINE_VERSIONS["code"]`.
- `search/symbols.py` parses the query (name, kind hint, parent, arity) and matches exact > prefix > fragment.
  New language: add a sample to `SAMPLES` in `tests/test_symbols.py` first, then fix the rules until it passes.

## PDFs and text in images (Phase 11)

- `extractors/pdf.py` (PDFKit) and `extractors/vision.py` (Apple Vision OCR) use PyObjC and are imported **only in the
  worker** (lazily); the daemon must never import them. OCR reads the original file (up to 4096 px, transparency on
  white), not the 1024 px embedding copy. The gate (fast pass, ≥ 2 words of 3+ letters) keeps ordinary photos cheap.
- PDF chunks carry `page` (schema v4); OCR text is stored as `ocr` chunks of the image, with text vectors, and joins
  the photos section in `hybrid.photo_results` (by words always, by meaning only for descriptive queries).
- `textnorm`: compounds with separators (`187,15`, `FV/2025/09/0932`) are phrase terms: retrieved with their parts,
  but coverage needs them adjacent.

## Photos and video frames (Phase 7)

- Two models per photo: EmbeddingGemma 2 vision tower (`IMAGE_TOKENS = 140`, `embedding/processor.py` builds the
  numpy/PIL processor without torch) and SigLIP 2 base (`embedding/siglip.py`: MLX fp16 image tower in the worker via
  `siglip_vision.py`, MLX 8-bit text tower in the daemon, loaded on demand and released after `QUERY_MODEL_IDLE_S`).
  Their vectors go to `vec_images_<model_id>` tables, never into the text tables.
- `extractors/image.py` (Pillow, HEIC via `sips`), `extractors/frames.py` (`lantern-media frames`: AVFoundation, scene
  score ×2 calibrated to ffmpeg's select; ffmpeg fallback; 30 s floor, dHash + colour signature dedup). Key frames also go through OCR (`vision.frame_text`): `screen` chunks (text vectors,
  frame moment, repeated text merged), searched with the files. Both models are optional at runtime: without SigLIP downloaded, EG2 alone is used.

## Service (`lantern/service/`, Phase 5)

- `core.py` — `Daemon`: API server (Unix socket, JSON lines, `api_<method>` handlers), control loop (watcher scans,
  power policy, worker start/stop), ask/auto approval. The text model lives on **one dedicated thread**
  (`model_thread`): MLX GPU streams are per-thread, so never call the embedder from API threads directly.
- `worker.py` — index worker process (`python -m lantern.service.worker`): background priority, drains the queue,
  exits; SIGTERM = finish current file, return the rest to the queue. Heavy models live only here.
- `tree.py` — settings tree: one folder level with state/reason per entry (`_Classifier`, same rules as the scanner),
  name search, check/uncheck as path exclusions (`set_included`). `perf.py` — memory sampler (footprint +
  lifetime peak via `proc_pid_rusage`, offsets of `rusage_info_v4`), worker models from progress, model sizes.
  APIs: `tree`, `tree_search`, `tree_preview`, `tree_set`, `extensions`, `extension_suggestions`, `perf`, `preview`
  (indexed text/code files only).
- Scanner filters: `walk.Filters` (ignored extensions, photos, audio/video) from `settings.walk_options()`; pass
  them everywhere `scan_root` / `iter_files` is called. The mass-deletion guard counts only files gone from disk.
- `watcher.py` (FSEvents via watchdog, debounce), `power.py` (IOKit power source, NSProcessInfo Low Power Mode and
  thermal state via ctypes, `pmset` as cross-check), `qos.py` (per-thread QoS: model thread user-initiated, scans and
  the control loop utility; never background in the daemon, it embeds queries), `settings.py` (`config.toml`),
  `notify.py` (osascript), `launchagent.py` (plist via plistlib, `launchctl bootstrap gui/<uid>`, `Interactive`).
- Changing the CLI (commands, options, output, exit codes): update `docs/CLI.md` and the man page `docs/lantern.1`
  (`tests/test_smoke.py` fails when the man page misses a command or option; lint with `mandoc -T lint`).
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

# Daemon (`daemon/lantern`)

All indexing and search logic lives here. Clients (CLI, Swift app) never touch the DB directly.

## Commands

```bash
uv run lantern index <path>     # scan + index changes (--force to allow purging > 50% of a root)
uv run lantern search "<query>" # vector list + keyword list (RRF fusion in Phase 3)
uv run lantern status
LANTERN_HOME=/tmp/x LANTERN_EMBEDDER=fake uv run lantern index <path>   # isolated DB, no model
uv run pytest tests/            # from repo root
```

## Database

Schema: `lantern/store/schema.sql` (version in `PRAGMA user_version`; bump + migrate on change). Single SQLite file, WAL mode, `sqlite-vec` loaded per connection.

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

scan (`scanner/diff.py`) enqueues jobs → `run_queue` claims up to 32 files → extract + chunk each → one embedding call
for all their chunks → one transaction per file. Data-like files (CSV/TSV, mostly numbers) are indexed head-only;
lockfiles are excluded (`config.py`). Changing chunking rules does not re-index existing files yet (open question 12).

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

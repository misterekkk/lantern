# Phase 2 plan: core (CLI, one folder)

**Status:** done (2026-10-15); results in DECISIONS.md, section "Phase 2: results". Scope: text and code. PDF and
audio were planned for Phase 4, RRF and index hygiene for Phase 3.

## Goal

Working end to end: `lantern index ~/project` → `lantern search "decision tree optimisation"` returns e.g. the
README that talks about it (vectors) and the files with the exact word (FTS as a separate list). `lantern status`
shows the numbers of files, chunks, jobs and errors.

## Modules and files

| Module | File(s) | Scope |
|---|---|---|
| store | `store/db.py`, `store/schema.sql`, `store/migrations.py` | connection (WAL, sqlite-vec, `foreign_keys`), `PRAGMA user_version` as the schema version, location `~/Library/Application Support/Lantern/index.db` (overridable with `LANTERN_HOME` in tests) |
| scanner | `scanner/walk.py`, `scanner/ignore.py`, `scanner/diff.py` | recursive walk without following symlinks outside the root, default exclusions + `.gitignore` (`pathspec`), diff with the database: new / changed / deleted / moved |
| queue | `queue/jobs.py` | upsert on `(file_id, kind)`, batch fetching, `running` → `queued` after a crash, at most 3 attempts |
| extractors | `extractors/text.py` | binary detection (NUL in the first 8 KB), size limit (2 MB by default), UTF-8 with a `charset-normalizer` fallback, mapping extensions to `text` or `code` |
| chunking | `chunking/code.py`, `chunking/text.py` | code: tree-sitter (`tree-sitter-language-pack`) by functions and classes for Python/JS/TS/Go/Rust/Java/Swift/C/C++; fallback and text: ~400-token windows with 15% overlap (Markdown by headings) |
| embedding | `embedding/base.py`, `embedding/mlx_backend.py`, `embedding/fake.py` | `Embedder` protocol; MLX 8-bit, lazy loading, text tower only in Phase 2; a deterministic fake for tests |
| search | `search/basic.py` | vector KNN (sqlite-vec) + FTS5 as separate lists; replaced by RRF in Phase 3 |
| cli | `cli.py` | `index <path>` (scan, queue, processing with a progress bar), `search "<q>" [-n]`, `status` |

## Key behaviour

- **Diff:** (path, size, mtime_ns). If they changed, hash the content (blake2b from hashlib). Same hash = metadata
  update only.
- **Move:** a file disappeared from path A and a file with the same hash appeared at B. Repoint `files.path` and
  update the FTS path, without re-embedding.
- **Mass-deletion guard:** if the root does not exist or is not readable, the scan ends with a warning and removes
  nothing. Also: if more than 50% of a root's files vanished and the root is on an external volume, require `--force`.
- **Indexing a file = one transaction:** delete the old chunks, FTS rows and vectors, then insert the new ones.
  Repeating it gives the same result.
- **Prompts:** documents `title: <relative path>[::symbol] | text: …`, queries `task: search result | query: …`.
- **Polish FTS normalisation** (ł→l) already in Phase 2, because it is one function and affects the schema.
- **RAM:** embedding batches of 8 chunks (peak ~0.6 GB instead of ~1.1 GB), `mx.clear_cache()` after each batch.

## Tests

- `test_scanner.py`: new, changed (mtime and content), only mtime changed (no re-index), deleted, moved (without
  calling the embedder, checked with a counting fake), secret file skipped, `.gitignore` respected.
- `test_guard.py`: unavailable root → zero deletions; after reconnecting → a normal diff.
- `test_queue.py`: idempotent upsert, resume after a "crash", one failing file does not stop the rest, attempt limit.
- `test_store.py`: deleting a file clears chunks, FTS and vectors; `model_id` separates the vector tables.
- `test_chunking.py`: functions and classes from Python/TS samples, window fallback.
- `@pytest.mark.model`: MLX vs the sentence-transformers reference (cos ≥ 0.999), a search sanity check on a small
  test repo, 0 network connections while loading and embedding (socket interception).

## New dependencies

`pathspec`, `tree-sitter-language-pack`, `charset-normalizer` (all through uv, listed in the README).

## Done when

1. `uv run pytest` and `uv run pytest -m model` pass, `ruff check` is clean.
2. Manual test on this repository and one real project: indexing time, database size, peak RAM, 5 Polish/English
   queries with results, **query latency** (target ~100–200 ms with the model loaded, for a future Spotlight-like panel).
3. README updated (commands, exclusions), DECISIONS.md extended with the new findings.

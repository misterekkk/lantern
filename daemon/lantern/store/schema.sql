-- Lantern index schema (version 1). Requires the sqlite-vec extension.
-- Vector tables are created per embedding model at runtime: vec_chunks_<embedding_models.id> (see store/db.py).

-- Indexed root folders. A root that is unavailable must never trigger a purge.
CREATE TABLE IF NOT EXISTS roots (
    id          INTEGER PRIMARY KEY,
    path        TEXT NOT NULL UNIQUE,
    last_scan   INTEGER                      -- unix epoch seconds
);

CREATE TABLE IF NOT EXISTS files (
    id            INTEGER PRIMARY KEY,
    root_id       INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
    path          TEXT NOT NULL UNIQUE,      -- absolute path
    size          INTEGER NOT NULL,
    mtime_ns      INTEGER NOT NULL,
    content_hash  TEXT NOT NULL,
    kind          TEXT NOT NULL,             -- text | code (pdf | audio later)
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending | indexed | error | skipped
    error         TEXT,
    indexed_at    INTEGER
);
CREATE INDEX IF NOT EXISTS files_root ON files(root_id);
CREATE INDEX IF NOT EXISTS files_hash ON files(content_hash);

-- One embedding model = one row. Vectors from different models are never mixed.
CREATE TABLE IF NOT EXISTS embedding_models (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,                 -- e.g. mlx-community/embeddinggemma-2-8bit
    revision  TEXT NOT NULL,                 -- pinned HF revision
    backend   TEXT NOT NULL,                 -- mlx | sentence-transformers | fake
    dtype     TEXT NOT NULL,                 -- 8bit | bf16 | ...
    dim       INTEGER NOT NULL,
    UNIQUE (name, revision, backend, dtype, dim)
);

CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY,
    file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    ord         INTEGER NOT NULL,            -- position within the file
    kind        TEXT NOT NULL,               -- text | code (audio | transcript later)
    start_line  INTEGER,
    end_line    INTEGER,
    start_ms    INTEGER,                     -- audio windows / transcript segments (Phase 4)
    end_ms      INTEGER,
    symbol      TEXT,                        -- function/class name for code
    text        TEXT                         -- NULL for pure audio windows
);
CREATE INDEX IF NOT EXISTS chunks_file ON chunks(file_id);

-- Keyword search; rowid = chunks.id. Values are folded (textnorm.fold) before insert.
-- No FK support on virtual tables: rows are deleted explicitly together with their chunks.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, path, symbol,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,               -- index (transcribe later)
    status      TEXT NOT NULL DEFAULT 'queued',  -- queued | running | done | failed
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    updated_at  INTEGER NOT NULL,
    UNIQUE (file_id, kind)                   -- idempotent enqueue (upsert)
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, kind);

-- Data collection for a future reranker (filled from Phase 3).
CREATE TABLE IF NOT EXISTS search_events (
    id               INTEGER PRIMARY KEY,
    ts               INTEGER NOT NULL,
    query            TEXT NOT NULL,
    results_json     TEXT NOT NULL,          -- returned chunks with features (ranks, scores, source)
    opened_chunk_id  INTEGER
);

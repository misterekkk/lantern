-- Lantern index schema (version 4; migrations from older versions live in store/db.py). Requires the sqlite-vec extension.
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
    indexed_at    INTEGER,
    pipeline_version INTEGER NOT NULL DEFAULT 0,  -- chunking/prompt rules used; mismatch -> reindex
    model_id      INTEGER                        -- embedding model of its vectors; mismatch -> reindex
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
    page        INTEGER,                     -- PDF page (1-based)
    text        TEXT                         -- NULL for pure audio windows
);
CREATE INDEX IF NOT EXISTS chunks_file ON chunks(file_id);

-- Keyword search; rowid = chunks.id. Values are folded (textnorm.fold) before insert.
-- No FK support on virtual tables: rows are deleted explicitly together with their chunks.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, path, symbol,
    tokenize = 'unicode61 remove_diacritics 2'
);
-- Overwrite deleted tokens in the FTS index instead of leaving them until the next merge.
INSERT INTO chunks_fts(chunks_fts, rank) VALUES ('secure-delete', 1);

-- Code definitions with their exact line (tree-sitter), for "where is add_two defined" search.
-- name_key: style-insensitive name (add_two / addTwo -> addtwo). Regular table: rows go with their file (CASCADE).
CREATE TABLE IF NOT EXISTS symbols (
    id          INTEGER PRIMARY KEY,
    file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    name_key    TEXT NOT NULL,
    kind        TEXT NOT NULL,               -- function | method | class | struct | interface | enum | ...
    parent      TEXT,                        -- enclosing class/module
    signature   TEXT NOT NULL,
    line        INTEGER NOT NULL,
    end_line    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS symbols_key ON symbols(name_key);
CREATE INDEX IF NOT EXISTS symbols_file ON symbols(file_id);

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

-- Applications (lantern/apps.py): names, category and one text vector each; rebuilt from the bundles.
CREATE TABLE IF NOT EXISTS apps (
    path       TEXT PRIMARY KEY,
    name       TEXT NOT NULL,      -- as Finder shows it
    names      TEXT NOT NULL,      -- JSON list: bundle names and translations in the preferred languages
    keywords   TEXT NOT NULL,      -- JSON list (MDItemKeywords)
    category   TEXT,               -- App Store category, e.g. social-networking
    bundle_id  TEXT,
    mtime      REAL NOT NULL,
    languages  TEXT NOT NULL,      -- preferred languages the names were read for
    document   TEXT NOT NULL,      -- the text the vector was made of
    model_id   INTEGER NOT NULL REFERENCES embedding_models(id),
    embedding  BLOB NOT NULL       -- float32, only compared with query vectors of the same model_id
);

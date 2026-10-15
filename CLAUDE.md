# Lantern: agent guide

Local, offline macOS app for semantic search over files (text, code, documents, audio) using EmbeddingGemma 2.
Architecture: a daemon holds all logic, clients (CLI now, Swift menu-bar app later) talk to it over an API.
Current phase and decisions: `docs/DECISIONS.md`. Architecture: `docs/ARCHITECTURE.md`.

## Working rules

1. Work in phases (see `docs/ARCHITECTURE.md`). After each phase: stop, show results, run tests. Do not start Phase 6 (Swift app) without explicit approval.
2. Do not assume what new libraries/models can do: verify, and record unverifiable things as open questions in `docs/DECISIONS.md`.
3. Install nothing globally without documenting it in README. Use `uv` and the project `.venv`.
4. Language: code, comments, identifiers and CLAUDE.md files in English; `README.md` and `docs/` in Polish.
5. Keep README in sync with reality each phase; never document features that do not exist yet.
6. Machine has 16 GB RAM: run at most one model process at a time; stop background servers you started.

## Hard privacy requirements

- Everything local and offline after the one-time model download. No telemetry, no external API calls.
- Model libraries run with `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1`.
- Index DB lives in `~/Library/Application Support/Lantern/` (not iCloud-synced).
- Default exclusions: `.git`, `node_modules`, build/cache dirs, secrets (`.env*`, `*.pem`, `*.p12`, `*.key`, `id_*`, token files); respect `.gitignore`.
- API only via a Unix socket with user-only permissions (0600), or localhost + token.
- Deleted data must really disappear: delete chunks+FTS+vectors in one transaction, `VACUUM` periodically.

## Never

- Modify, move or delete indexed user files (Lantern is read-only towards them).
- Add telemetry, analytics, crash reporting or any outbound network call at runtime.
- Mix vectors from different `model_id`s (name, revision, backend, quantization, dim). Model change = reindex.
- Purge index entries of a root that is currently unavailable (unmounted disk, network share).
- Implement the LightGBM reranker or add Treelite (only collect `search_events` for now).

## Layout

```
daemon/lantern/   api/ scanner/ queue/ extractors/ chunking/ embedding/ store/ search/ cli.py
daemon/CLAUDE.md  daemon conventions (schema, queue, adding extractors/backends)
app/              Swift menu-bar client (Phase 6, placeholder)
launchd/          LaunchAgent plist template
spikes/phase0/    Phase 0 measurement scripts (not app code, excluded from ruff)
tests/            pytest; tests needing the real model are marked `@pytest.mark.model`
```

## Commands

```bash
eval "$(/opt/homebrew/bin/brew shellenv)"   # brew/uv/ffmpeg are not on PATH by default on this machine
uv sync                      # install deps (add --group spike for spikes/phase0)
uv run pytest                # fast tests (excludes -m model)
uv run pytest -m model       # tests with the real embedding model
uv run ruff check            # lint (ruff format for formatting)
uv run lantern --help        # CLI
uv run lantern daemon run    # daemon in the foreground (LANTERN_HOME / LANTERN_SOCKET for an isolated test instance)
```

## Conventions

- Python 3.12, type hints, stdlib `sqlite3` + `sqlite-vec` (no ORM), `argparse` for the CLI.
- Embedding backend: MLX via pinned `mlx-vlm` commit + `mlx-community/embeddinggemma-2-8bit` (ADR-001). Text prompts:
  queries `task: search result | query: …`, documents `title: {path or none} | text: …`, code queries `task: code retrieval | query: …`.
- MLX: always set memory/cache limits at load (unbounded, indexing hit 13 GB on 16 GB RAM). MLX GPU streams are
  per-thread: load and call a model on the same thread (the daemon uses one dedicated model thread).
- Never install the LaunchAgent or touch `~/Library/LaunchAgents` without the user's explicit approval.
- Always set the audio `max_length` explicitly (the default processor silently truncates at 30 s). Never use float16.
- Normalise Polish text (ł→l) before FTS insert and in queries.
- Tests must not need the network or the real model unless marked `model`; use a deterministic fake embedder.

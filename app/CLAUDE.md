# App (Swift menu-bar client): placeholder

Phase 6. Do not start without explicit approval from the user.

- SwiftUI `MenuBarExtra`. A **thin client of the daemon API** (Unix socket): status, queue, start/stop/reindex,
  search window, folder configuration.
- No indexing, embedding or database logic here. Everything goes through the daemon.
- Possible future option (unverified): native MLX Swift port of EmbeddingGemma 2 (see `docs/DECISIONS.md`, open questions).

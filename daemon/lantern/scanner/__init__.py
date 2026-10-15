"""Filesystem scan (source of truth) and FSEvents watcher (optimisation only).

Responsibilities: diff files against the index by (path, size, mtime), hash only when needed,
detect moves by content hash, never purge entries of an unavailable root."""

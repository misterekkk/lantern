"""Paths and defaults. Everything user-facing lives under one directory outside iCloud."""

import os
from pathlib import Path

# Offline-only model libraries (set before any huggingface/transformers import).
OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "TRANSFORMERS_NO_ADVISORY_WARNINGS": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
}


def enforce_offline() -> None:
    for key, value in OFFLINE_ENV.items():
        os.environ[key] = value


def lantern_home() -> Path:
    """Data directory; override with LANTERN_HOME (tests, custom setups)."""
    override = os.environ.get("LANTERN_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / "Lantern"


def db_path() -> Path:
    return lantern_home() / "index.db"


# Directory names never descended into.
EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "renv",  # R package library (like a virtualenv)
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".gradle",
        ".idea",
        ".next",
        ".nuxt",
        ".cache",
        "build",
        "dist",
        "target",
        "out",
        "DerivedData",
        "Pods",
        ".build",
        ".terraform",
        "coverage",
        "vendor",  # Go modules / Composer dependencies
        "bower_components",
        ".dart_tool",
        ".svelte-kit",
        ".parcel-cache",
        ".turbo",
    }
)

# Files that may contain secrets (gitignore-style patterns, matched on the file name).
SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "*.env",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "*.crt",
    "*.cer",
    "*.der",
    "*.p8",
    "*.mobileprovision",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "*.ppk",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    "credentials",
    "credentials.json",
    "*credentials*.json",
    "service-account*.json",
    "*serviceaccount*.json",
    "secrets.*",
    "*.secrets",
    "*secret*.json",
    "*secret*.yaml",
    "*secret*.yml",
    "*token*.json",
    "*token*.txt",
    "*.kdbx",
    "*.gpg",
    "*.asc",
    "*.tfstate",
    "*.tfvars",
)

MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024

# Audio / video soundtrack (Phase 4, ADR-003).
AUDIO_SAMPLE_RATE = 16_000
AUDIO_WINDOW_S = 30  # EmbeddingGemma 2 audio window; the processor cap is always set explicitly
AUDIO_HOP_S = 25  # 5 s overlap so a phrase on a window boundary is not lost
AUDIO_MIN_WINDOW_S = 1.0
TRANSCRIPT_CHUNK_S = 20  # Whisper segments grouped into ~20 s chunks (tighter timestamps in results)
MAX_TRANSCRIBE_S = 4 * 3600  # Whisper decodes the whole file into memory (~230 MB per hour)

# Media files above this size are hashed by samples (size + head/middle/tail), not in full.
PARTIAL_HASH_BYTES = 64 * 1024 * 1024

# Generated files with no search value (exact names).
GENERATED_FILES = frozenset(
    {
        "package-lock.json",
        "npm-shrinkwrap.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "bun.lockb",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "Cargo.lock",
        "Gemfile.lock",
        "composer.lock",
        "go.sum",
        "Podfile.lock",
        "Package.resolved",
        "flake.lock",
    }
)

# Mass-deletion guard: refuse to purge more than this fraction of a root's files without --force.
MASS_DELETE_FRACTION = 0.5
MASS_DELETE_MIN_FILES = 20

"""Paths and defaults. Everything user-facing lives under one directory outside iCloud."""

import os
from pathlib import Path

# Offline-only model libraries (set before any huggingface/transformers import).
OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
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

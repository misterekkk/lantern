"""Content extractors (text, code; PDF and audio later). Read-only access to user files."""

from pathlib import Path

# Extension -> tree-sitter language name (tree-sitter-language-pack).
CODE_LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".m": "objc",
    ".mm": "objc",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".scala": "scala",
    ".lua": "lua",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".sql": "sql",
    ".r": "r",
    ".dart": "dart",
    ".ex": "elixir",
    ".exs": "elixir",
    ".hs": "haskell",
}

TEXT_EXTENSIONS = frozenset(
    {
        ".md",
        ".markdown",
        ".txt",
        ".rst",
        ".adoc",
        ".org",
        ".tex",
        ".csv",
        ".tsv",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".xml",
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".less",
        ".vue",
        ".svelte",
        ".gradle",
        ".properties",
        ".proto",
        ".graphql",
        ".ipynb",
        ".srt",
        ".vtt",
        ".log",
    }
)

TEXT_FILENAMES = frozenset(
    {
        "readme",
        "license",
        "makefile",
        "dockerfile",
        "jenkinsfile",
        "procfile",
        "gemfile",
        "rakefile",
        "cmakelists.txt",
        "changelog",
        "authors",
        "contributing",
    }
)


def kind_for(path: Path) -> str | None:
    """'code', 'text' or None (not indexed in this phase)."""
    suffix = path.suffix.lower()
    if suffix in CODE_LANGUAGES:
        return "code"
    if suffix in TEXT_EXTENSIONS or path.name.lower() in TEXT_FILENAMES:
        return "text"
    return None


def language_for(path: Path) -> str | None:
    return CODE_LANGUAGES.get(path.suffix.lower())

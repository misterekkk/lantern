"""Content extractors (text, code, audio and video soundtracks). Read-only access to user files."""

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


AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".oga", ".opus", ".aac", ".aif", ".aiff", ".caf", ".wma", ".amr"}
)
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv", ".mpg", ".mpeg", ".3gp"})


def kind_for(path: Path) -> str | None:
    """'code', 'text', 'audio', 'video' or None (not indexed)."""
    suffix = path.suffix.lower()
    if suffix in CODE_LANGUAGES:
        return "code"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in TEXT_EXTENSIONS or path.name.lower() in TEXT_FILENAMES:
        return "text"
    return None


def language_for(path: Path) -> str | None:
    return CODE_LANGUAGES.get(path.suffix.lower())

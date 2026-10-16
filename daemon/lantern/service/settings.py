"""User settings in config.toml (data directory). Missing keys fall back to defaults."""

import tomllib
from dataclasses import asdict, dataclass, field, fields  # noqa: F401 - asdict re-exported for the CLI
from pathlib import Path

from lantern.config import lantern_home

INDEX_MODES = ("ask", "auto")


@dataclass
class Settings:
    index_mode: str = "ask"  # ask: new/changed files wait for approval; auto: index right away
    pause_on_battery: bool = True
    pause_on_low_power: bool = True  # Low Power Mode is on: the user is saving energy
    pause_on_thermal: bool = True
    transcribe: bool = True
    index_photos: bool = True
    index_video_frames: bool = True
    index_audio_video: bool = True  # audio files and videos (sound, transcripts, frames)
    index_image_text: bool = True  # read text in photos and screenshots (Apple Vision OCR, on device)
    notify: bool = True
    debounce_s: float = 3.0  # file events are grouped for this long before a rescan
    # Extra exclusions: absolute paths (~ allowed) or .gitignore-style patterns matched inside every indexed folder.
    exclude: list[str] = field(default_factory=list)
    # File extensions never indexed (normalised: ".csv"). Notebooks and data tables are mostly output and numbers.
    exclude_extensions: list[str] = field(default_factory=lambda: [".ipynb", ".csv", ".tsv"])

    def validate(self) -> None:
        if self.index_mode not in INDEX_MODES:
            raise ValueError(f"index_mode must be one of {INDEX_MODES}")
        if self.debounce_s < 0:
            raise ValueError("debounce_s must be >= 0")


def path() -> Path:
    return lantern_home() / "config.toml"


def load() -> Settings:
    settings = Settings()
    if path().exists():
        data = tomllib.loads(path().read_text())
        for f in fields(Settings):
            if f.name in data:
                current = getattr(settings, f.name)
                value = data[f.name]
                setattr(
                    settings, f.name, [str(v) for v in value] if isinstance(current, list) else type(current)(value)
                )
    settings.validate()
    return settings


def save(settings: Settings) -> None:
    settings.validate()
    lines = ["# Lantern settings (managed by `lantern config` and the app)"]
    for key, value in asdict(settings).items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, str):
            lines.append(f"{key} = {_toml_str(value)}")
        elif isinstance(value, list):
            lines.append(f"{key} = [" + ", ".join(_toml_str(v) for v in value) + "]")
        else:
            lines.append(f"{key} = {value}")
    path().parent.mkdir(parents=True, exist_ok=True)
    path().write_text("\n".join(lines) + "\n")


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def update(settings: Settings, key: str, raw: str) -> Settings:
    """Set one key from a string (CLI / API)."""
    names = {f.name: f for f in fields(Settings)}
    if key not in names:
        raise KeyError(f"unknown setting {key!r}; known: {', '.join(names)}")
    current = getattr(settings, key)
    if isinstance(current, list):
        raise ValueError(f"{key} is a list; use `lantern exclude add/remove`")
    if isinstance(current, bool):
        if raw.lower() not in ("true", "false", "1", "0", "yes", "no"):
            raise ValueError(f"{key} expects true/false")
        value = raw.lower() in ("true", "1", "yes")
    else:
        value = type(current)(raw)
    setattr(settings, key, value)
    settings.validate()
    return settings


def walk_options(settings: Settings) -> dict:
    """Keyword arguments for scan_root / iter_files derived from the settings."""
    return {
        "excludes": settings.exclude, "include_images": settings.index_photos,
        "skip_extensions": settings.exclude_extensions, "include_media": settings.index_audio_video,
    }  # fmt: skip


def add_extension(settings: Settings, raw: str) -> str:
    from lantern.scanner.walk import normalize_extension

    ext = normalize_extension(raw)
    if ext is None:
        raise ValueError(f"{raw!r} is not a file extension")
    if ext not in settings.exclude_extensions:
        settings.exclude_extensions.append(ext)
    return ext


def remove_extension(settings: Settings, raw: str) -> str:
    from lantern.scanner.walk import normalize_extension

    ext = normalize_extension(raw) or raw
    if ext not in settings.exclude_extensions:
        raise KeyError(f"{ext!r} is not in the ignored extensions")
    settings.exclude_extensions.remove(ext)
    return ext


def add_exclude(settings: Settings, pattern: str) -> Settings:
    if pattern not in settings.exclude:
        settings.exclude.append(pattern)
    return settings


def remove_exclude(settings: Settings, pattern: str) -> Settings:
    if pattern not in settings.exclude:
        raise KeyError(f"{pattern!r} is not in the exclude list")
    settings.exclude.remove(pattern)
    return settings

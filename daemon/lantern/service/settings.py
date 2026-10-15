"""User settings in config.toml (data directory). Missing keys fall back to defaults."""

import tomllib
from dataclasses import asdict, dataclass, fields  # noqa: F401 - asdict re-exported for the CLI
from pathlib import Path

from lantern.config import lantern_home

INDEX_MODES = ("ask", "auto")


@dataclass
class Settings:
    index_mode: str = "ask"  # ask: new/changed files wait for approval; auto: index right away
    pause_on_battery: bool = True
    pause_on_thermal: bool = True
    transcribe: bool = True
    notify: bool = True
    debounce_s: float = 3.0  # file events are grouped for this long before a rescan

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
                setattr(settings, f.name, type(getattr(settings, f.name))(data[f.name]))
    settings.validate()
    return settings


def save(settings: Settings) -> None:
    settings.validate()
    lines = ["# Lantern settings (managed by `lantern config` and the app)"]
    for key, value in asdict(settings).items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, str):
            lines.append(f'{key} = "{value}"')
        else:
            lines.append(f"{key} = {value}")
    path().parent.mkdir(parents=True, exist_ok=True)
    path().write_text("\n".join(lines) + "\n")


def update(settings: Settings, key: str, raw: str) -> Settings:
    """Set one key from a string (CLI / API)."""
    names = {f.name: f for f in fields(Settings)}
    if key not in names:
        raise KeyError(f"unknown setting {key!r}; known: {', '.join(names)}")
    current = getattr(settings, key)
    if isinstance(current, bool):
        if raw.lower() not in ("true", "false", "1", "0", "yes", "no"):
            raise ValueError(f"{key} expects true/false")
        value = raw.lower() in ("true", "1", "yes")
    else:
        value = type(current)(raw)
    setattr(settings, key, value)
    settings.validate()
    return settings

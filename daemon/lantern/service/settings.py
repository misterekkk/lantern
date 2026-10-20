"""User settings in config.toml (data directory). Missing keys fall back to defaults."""

import tomllib
from dataclasses import asdict, dataclass, field, fields  # noqa: F401 - asdict re-exported for the CLI
from pathlib import Path

from lantern.config import lantern_home

INDEX_MODES = ("ask", "auto")
# the search model stays in memory, is released after a few idle minutes, or stays on the charger and is released
# when idle on battery
MODES = ("ready", "on_demand", "battery")
SCHEDULES = ("anytime", "idle", "night")  # when indexing runs; search always works


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
    mode: str = "ready"  # MODES; the welcome window suggests one from the Mac's memory
    places: bool = True  # GPS of photos and videos -> city (bundled GeoNames table, offline); off: coordinates deleted
    people: bool = True  # people in photos: how many, women/men by appearance (Vision + photo model); off: deleted
    notify: bool = True
    schedule: str = "anytime"  # SCHEDULES: idle = after IDLE_MINUTES without input; night = night_from..night_to
    night_from: int = 22  # hour, local time
    night_to: int = 7
    debounce_s: float = 3.0  # file events are grouped for this long before a rescan
    # Extra exclusions: absolute paths (~ allowed) or .gitignore-style patterns matched inside every indexed folder.
    exclude: list[str] = field(default_factory=list)
    # File extensions never indexed (normalised: ".csv"). Notebooks and data tables are mostly output and numbers.
    exclude_extensions: list[str] = field(default_factory=lambda: [".ipynb", ".csv", ".tsv"])
    # Languages added on top of the macOS ones (app names, text in images); codes like "de" or "zh-Hans".
    extra_languages: list[str] = field(default_factory=list)

    def validate(self) -> None:
        for key, options in CHOICES.items():
            if getattr(self, key) not in options:
                raise ValueError(f"{key} must be one of {options}")
        for key, (low, high) in RANGES.items():
            if not low <= getattr(self, key) <= high:
                raise ValueError(f"{key} must be between {low} and {high}")


def path() -> Path:
    return lantern_home() / "config.toml"


def defaults_for(profile: str) -> Settings:
    """First-launch defaults: on an 8 GB Mac ("light") the heavy optional work is off (Whisper, video frames), and
    the search model is released when idle; switching them on later shows what they need (phase 17)."""
    s = Settings()
    if profile == "light":
        s.transcribe = False
        s.index_video_frames = False
        s.mode = "on_demand"
    return s


# Hand-edited numbers are clamped into a range that works (a 0 s debounce rescans on every write, hours never).
RANGES = {"debounce_s": (0.1, 60.0), "night_from": (0, 23), "night_to": (0, 23)}
CHOICES = {"index_mode": INDEX_MODES, "mode": MODES, "schedule": SCHEDULES}
IDLE_MINUTES = 5
problems: list[str] = []  # what the last load() had to correct in config.toml (the daemon reports it)


def restored(current: Settings) -> Settings:
    """This Mac's first-launch defaults, keeping the parts of folders left out: they are the user's choices of what
    Lantern may read, and restoring them would let it read more."""
    from lantern.service import machine

    fresh = defaults_for(machine.profile())
    fresh.exclude = list(current.exclude)
    return fresh


def load() -> Settings:
    """config.toml, or this Mac's defaults on first launch. A hand-edited file never stops Lantern: an unreadable
    file, a wrong type or an unknown choice falls back to the default, a number out of range is clamped, and each
    correction is listed in `problems`."""
    from lantern.service import machine

    problems.clear()
    settings = defaults_for(machine.profile())
    if not path().exists():  # first launch: defaults for this Mac's memory
        return settings
    settings = Settings()  # a saved file: keys missing from it take the plain defaults, as before
    try:
        data = tomllib.loads(path().read_text())
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        problems.append(f"config.toml could not be read ({exc}); using the defaults")
        return settings
    names = {f.name for f in fields(Settings)}
    for key in data:
        if key not in names:
            problems.append(f"{key}: unknown setting, ignored")
    for f in fields(Settings):
        if f.name not in data:
            continue
        value = _coerce(getattr(settings, f.name), data[f.name])
        if value is None:
            problems.append(f"{f.name}: {data[f.name]!r} is not valid, using {getattr(settings, f.name)!r}")
            continue
        if f.name in CHOICES and value not in CHOICES[f.name]:
            problems.append(f"{f.name}: {value!r} is not one of {CHOICES[f.name]}, using {getattr(settings, f.name)!r}")
            continue
        if f.name in RANGES:
            low, high = RANGES[f.name]
            if not low <= value <= high:
                problems.append(f"{f.name}: {value} is outside {low}-{high}, using {min(max(value, low), high)}")
                value = min(max(value, low), high)
        setattr(settings, f.name, value)
    return settings


def _coerce(current, value):
    """value in the type of current, or None when it cannot be (TOML gives real types; strings are not guessed)."""
    if isinstance(current, bool):
        return value if isinstance(value, bool) else None
    if isinstance(current, list):
        return [v for v in value if isinstance(v, str)] if isinstance(value, list) else None
    if isinstance(current, int):
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    if isinstance(current, float):
        return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
    if isinstance(current, str):
        return value if isinstance(value, str) else None
    return None


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


def in_schedule(settings: Settings, hour: int, idle_s: float) -> bool:
    """Whether indexing may run now by the schedule (the other pauses are separate)."""
    if settings.schedule == "idle":
        return idle_s >= IDLE_MINUTES * 60
    if settings.schedule == "night":
        start, end = settings.night_from, settings.night_to
        return start <= hour < end if start < end else hour >= start or hour < end
    return True


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

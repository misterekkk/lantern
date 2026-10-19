"""Model catalogue and one-time downloading from Hugging Face (`python -m lantern.models`).

The daemon itself always runs with `HF_HUB_OFFLINE=1`. When the user installs the prebuilt app (`.dmg`) and clicks
"Download" in the menu bar window or in Settings → Performance, the daemon starts this module as a child process with
`HF_HUB_OFFLINE` unset (`HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1` stay set) to fetch the pinned revision into
`~/.cache/huggingface/hub/`, then loads the model offline.
"""

import json
import os
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepoSpec:
    key: str  # model key in PerfSnapshot.Model
    group: str  # required | whisper | photos
    repo: str
    revision: str
    size_bytes: int  # of the pinned revision, measured on the downloaded snapshot (fixed while the revision is)
    label: str

    @property
    def approx_mb(self) -> int:
        """Decimal megabytes, as Finder counts them; fractions cut off."""
        return self.size_bytes // 10**6


def catalogue() -> list[RepoSpec]:
    from lantern.embedding import siglip
    from lantern.embedding.mlx_backend import REPO, REVISION
    from lantern.transcription.whisper import REPO as WHISPER_REPO
    from lantern.transcription.whisper import REVISION as WHISPER_REVISION

    return [
        RepoSpec("embeddinggemma", "required", REPO, REVISION, 1_266_584_562, "EmbeddingGemma 2 (search, ~1.3 GB)"),
        RepoSpec("whisper", "whisper", WHISPER_REPO, WHISPER_REVISION, 1_613_979_758,
                 "Whisper large-v3-turbo (transcripts of recordings, ~1.6 GB)"),
        RepoSpec("siglip_image", "photos", siglip.IMAGE_REPO, siglip.IMAGE_REVISION, 1_539_458_338,
                 "SigLIP 2 image side (better photo search, ~1.5 GB)"),
        RepoSpec("siglip_text", "photos", siglip.TEXT_REPO, siglip.TEXT_REVISION, 438_241_882,
                 "SigLIP 2 text side (~0.4 GB)"),
    ]  # fmt: skip


def hub_dir() -> Path:
    from huggingface_hub.constants import HF_HUB_CACHE

    return Path(HF_HUB_CACHE)


def repo_dir(repo: str) -> Path:
    return hub_dir() / f"models--{repo.replace('/', '--')}"


def snapshot_dir(repo: str, revision: str) -> Path:
    return repo_dir(repo) / "snapshots" / revision


def is_downloaded(repo: str, revision: str) -> bool:
    snap = snapshot_dir(repo, revision)
    return snap.is_dir() and any(snap.glob("*.safetensors"))


def required_downloaded() -> bool:
    return all(is_downloaded(m.repo, m.revision) for m in catalogue() if m.group == "required")


def blob_bytes(repo: str) -> int:
    """Bytes currently present in the repo's cache folder (completed blobs + `.incomplete` downloads in progress)."""
    blobs = repo_dir(repo) / "blobs"
    if not blobs.is_dir():
        return 0
    total = 0
    with suppress(OSError):
        for entry in blobs.iterdir():
            with suppress(OSError):
                total += entry.stat().st_size
    return total


def resolve_targets(names: list[str]) -> list[RepoSpec]:
    """Expand group names (`required`, `whisper`, `photos`, `all`) or model keys (`embeddinggemma`, `siglip_image`, …)
    into the repos to download, in catalogue order."""
    wanted = set(names)
    if "all" in wanted:
        return catalogue()
    if "siglip" in wanted or "siglip_image" in wanted or "siglip_text" in wanted:
        wanted.add("photos")
    return [m for m in catalogue() if m.group in wanted or m.key in wanted]


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args or args == ["--list"]:
        for m in catalogue():
            print(m.group, m.repo, m.revision, m.label)
        return 0
    for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        os.environ.pop(var, None)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"

    from huggingface_hub import snapshot_download

    targets = [m for m in resolve_targets(args) if not is_downloaded(m.repo, m.revision)]
    for i, m in enumerate(targets, 1):
        print(json.dumps({"event": "ModelDownload", "key": m.key, "group": m.group, "repo": m.repo,
                          "step": i, "total": len(targets), "approx_mb": m.approx_mb, "size_mb": m.approx_mb}),
              flush=True)  # fmt: skip
        snapshot_download(m.repo, revision=m.revision)
    print(json.dumps({"event": "done", "downloaded": [m.key for m in targets]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

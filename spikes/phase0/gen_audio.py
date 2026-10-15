"""Phase 0 spike: render the synthetic corpus to 16 kHz mono WAV with `say` + ffmpeg."""

import json
import subprocess
import tempfile
from pathlib import Path

import soundfile as sf

from quality_data import LONG_SEGMENTS, RECORDINGS

OUT = Path(__file__).parent / "data" / "corpus"
VOICES = {"pl": "Zosia", "en": "Daniel"}
FFMPEG = "/opt/homebrew/bin/ffmpeg"


def render(text: str, lang: str, dst: Path) -> float:
    with tempfile.TemporaryDirectory() as tmp:
        aiff = Path(tmp) / "x.aiff"
        subprocess.run(["say", "-v", VOICES[lang], "-r", "165", "-o", str(aiff), text], check=True)
        subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", str(aiff), "-ar", "16000", "-ac", "1", str(dst)], check=True)
    return sf.info(dst).duration


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for rid, (lang, text) in RECORDINGS.items():
        manifest[rid] = {"lang": lang, "duration_s": round(render(text, lang, OUT / f"{rid}.wav"), 1)}
    # Long recording: render segments separately and concatenate, keeping boundaries.
    seg_files, segments, t = [], [], 0.0
    for sid, text in LONG_SEGMENTS:
        p = OUT / f"_seg_{sid}.wav"
        d = render(text, "pl", p)
        segments.append({"id": sid, "start_s": round(t, 1), "end_s": round(t + d, 1)})
        seg_files.append(p)
        t += d
    lst = OUT / "_concat.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in seg_files))
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(OUT / "pl_dlugie.wav")], check=True)
    for p in [*seg_files, lst]:
        p.unlink()
    manifest["pl_dlugie"] = {"lang": "pl", "duration_s": round(t, 1), "segments": segments}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(json.dumps(manifest, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

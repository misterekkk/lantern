# Phase 14 spike: a downloadable app without Homebrew

Does AVFoundation decode audio as well as ffmpeg for Lantern? Results in docs/DECISIONS.md ("Phase 14 spike").

```bash
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun swiftc -O spikes/phase14/avdecode.swift -o /tmp/avdecode
uv run python spikes/phase14/compare_audio.py /tmp/avdecode file.mp3 file.mp4 ...    # signal: shift, corr, SNR
uv run python spikes/phase14/compare_models.py /tmp/avdecode file.mp3 file.mp4 ...   # EG2 audio vectors, Whisper text
```

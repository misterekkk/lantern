#!/bin/bash
# The showcase as a video (for X and similar): WebKit renders every frame of site/public/showcase.svg at its exact
# time (render.swift), ffmpeg encodes 1080p H.264. Needs Xcode (swiftc) and ffmpeg; all offline.
set -euo pipefail
cd "$(dirname "$0")/../.."
eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null)" || true
FPS=${FPS:-30}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
python3 scripts/showcase/build.py
DEVELOPER_DIR=${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer} swiftc -O scripts/showcase/render.swift -o "$WORK/render"
"$WORK/render" site/public/showcase.svg "$WORK/frames" 1280 720 27 "$FPS"
mkdir -p build
ffmpeg -loglevel error -y -framerate "$FPS" -i "$WORK/frames/f%05d.png" \
  -vf "scale=1920:1080:flags=lanczos,format=yuv420p" -c:v libx264 -preset slow -crf 18 -movflags +faststart \
  build/lantern-showcase.mp4
echo "build/lantern-showcase.mp4"

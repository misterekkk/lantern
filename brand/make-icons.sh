#!/bin/bash
# Builds app/Resources/AppIcon.icns and the website's PNGs from brand/lantern-icon.svg (macOS renders the SVG).
set -euo pipefail
cd "$(dirname "$0")/.."
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
SET=$(mktemp -d)/AppIcon.iconset
mkdir -p "$SET"
render() { xcrun swift brand/svg2png.swift brand/lantern-icon.svg "$2" "$1"; }
for s in 16 32 128 256 512; do
  render $s "$SET/icon_${s}x${s}.png"
  render $((s * 2)) "$SET/icon_${s}x${s}@2x.png"
done
iconutil -c icns "$SET" -o app/Resources/AppIcon.icns
cp brand/lantern-icon.svg site/public/lantern-icon.svg 2>/dev/null || true
echo "built app/Resources/AppIcon.icns"

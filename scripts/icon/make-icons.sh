#!/bin/bash
# Writes every copy of Lantern's icon from scripts/icon/lantern-icon.svg (macOS renders the SVG, svg2png.swift adds
# the icon shadow): the app's AppIcon.icns, the website (logo, favicon, demo) and the launcher extensions' PNGs.
# The Raycast Store gets the new icon.png only through a pull request to raycast/extensions.
set -euo pipefail
cd "$(dirname "$0")/../.."
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
SVG=scripts/icon/lantern-icon.svg
render() { xcrun swift scripts/icon/svg2png.swift "$SVG" "$2" "$1"; }
SET=$(mktemp -d)/AppIcon.iconset
mkdir -p "$SET"
for s in 16 32 128 256 512; do
  render $s "$SET/icon_${s}x${s}.png"
  render $((s * 2)) "$SET/icon_${s}x${s}@2x.png"
done
iconutil -c icns "$SET" -o app/Resources/AppIcon.icns
for copy in site/public/lantern-icon.svg site/src/assets/lantern-icon.svg site/public/favicon.svg \
            site/public/demo/assets/lantern.svg; do
  cp "$SVG" "$copy"
done
render 512 integrations/raycast/assets/icon.png
render 512 integrations/alfred/icon.png
echo "built AppIcon.icns, the website's icons and the Raycast and Alfred icon.png"

#!/bin/zsh
# Builds app/build/Lantern.app with the Xcode toolchain (macOS 26 SDK for Liquid Glass) and signs it.
# Signing: the first "Apple Development" identity (Xcode → Settings → Accounts), otherwise ad-hoc.
# The project's Python (.venv) is baked into Info.plist so the app can start the daemon.
set -euo pipefail
SCRIPT="${0:A}"          # absolute before changing directory
REPO="${SCRIPT:h:h}"
cd "${SCRIPT:h}"
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
VERSION="$(grep -m1 '^version' "$REPO/pyproject.toml" | sed -E 's/.*"(.*)"/\1/')"

swift build -c release --product Lantern
swift build -c release --product lantern-media   # AVFoundation decoding for the daemon (no ffmpeg needed)

APP=build/Lantern.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$(swift build -c release --show-bin-path)/Lantern" "$APP/Contents/MacOS/Lantern"
cp "$(swift build -c release --show-bin-path)/lantern-media" "$APP/Contents/MacOS/lantern-media"
sed -e "s#__PYTHON__#$REPO/.venv/bin/python#" -e "s#__VERSION__#$VERSION#" -e "s#__BUILD__#$(date +%Y%m%d%H%M)#" \
    Resources/Info.plist.template > "$APP/Contents/Info.plist"
cp -R Resources/*.lproj "$APP/Contents/Resources/"   # localizations (the system language picks one)
cp Resources/AppIcon.icns "$APP/Contents/Resources/"  # from brand/lantern-icon.svg (brand/make-icons.sh)
/usr/bin/python3 ../integrations/alfred/build.py "$APP/Contents/Resources" >/dev/null  # Settings → Search from: Alfred

IDENTITY="$(security find-identity -v -p codesigning | grep -m1 'Apple Development' | sed -E 's/.*"(.*)"/\1/' || true)"
if [[ -n "$IDENTITY" ]]; then
  codesign --force --sign "$IDENTITY" "$APP/Contents/MacOS/lantern-media"  # nested code first, then the bundle
  codesign --force --sign "$IDENTITY" "$APP"
  echo "signed with: $IDENTITY"
else
  codesign --force --sign - "$APP/Contents/MacOS/lantern-media"
  codesign --force --sign - "$APP"
  echo "signed ad-hoc (sign in to Xcode → Settings → Accounts for a stable signature; macOS permissions then survive rebuilds)"
fi
codesign --verify "$APP"
echo "built $APP"

# --install: copy to /Applications (launch at login and Spotlight work best from there); quits a running copy first.
if [[ "${1:-}" == "--install" ]]; then
  if pgrep -xq Lantern; then
    osascript -e 'tell application id "com.lantern.app" to quit' >/dev/null 2>&1 || pkill -x Lantern || true
    for i in {1..20}; do pgrep -xq Lantern || break; sleep 0.5; done
  fi
  rm -rf /Applications/Lantern.app
  ditto "$APP" /Applications/Lantern.app
  echo "installed /Applications/Lantern.app"
fi

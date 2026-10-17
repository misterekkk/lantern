#!/bin/zsh
# Builds app/build/Lantern.app with the Xcode toolchain (macOS 26 SDK for Liquid Glass) and signs it.
# Signing: the first "Apple Development" identity (Xcode → Settings → Accounts), otherwise ad-hoc.
#
#   app/build.sh               dev bundle (.venv/bin/python in Info.plist)
#   app/build.sh --install     also copy Lantern.app to /Applications
#   app/build.sh --standalone  bundle Python 3.12 + packages + daemon inside Contents/Resources/python
#   app/build.sh --dmg         build a standalone, ad-hoc signed Lantern.app and pack build/Lantern.dmg
set -euo pipefail
SCRIPT="${0:A}"          # absolute before changing directory
REPO="${SCRIPT:h:h}"
cd "${SCRIPT:h}"
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
export CLANG_MODULE_CACHE_PATH="${CLANG_MODULE_CACHE_PATH:-/tmp/lantern-clang-cache}"
VERSION="$(grep -m1 '^version' "$REPO/pyproject.toml" | sed -E 's/.*"(.*)"/\1/')"

INSTALL=0 STANDALONE=0 DMG=0
for arg in "$@"; do
  case "$arg" in
    --install) INSTALL=1 ;;
    --standalone) STANDALONE=1 ;;
    --dmg) STANDALONE=1; DMG=1 ;;
    *) echo "usage: $0 [--install] [--standalone] [--dmg]" >&2; exit 2 ;;
  esac
done

swift build --disable-sandbox -c release --product Lantern
swift build --disable-sandbox -c release --product lantern-media   # AVFoundation decoding for the daemon (no ffmpeg needed)
BIN_DIR="$(swift build --disable-sandbox -c release --show-bin-path)"

APP=build/Lantern.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN_DIR/Lantern" "$APP/Contents/MacOS/Lantern"
cp "$BIN_DIR/lantern-media" "$APP/Contents/MacOS/lantern-media"

if (( STANDALONE )); then
  PYTHON_PLIST="python/bin/python3"
else
  PYTHON_PLIST="$REPO/.venv/bin/python"
fi
sed -e "s#__PYTHON__#$PYTHON_PLIST#" -e "s#__VERSION__#$VERSION#" -e "s#__BUILD__#$(date +%Y%m%d%H%M)#" \
    Resources/Info.plist.template > "$APP/Contents/Info.plist"
cp -R Resources/*.lproj "$APP/Contents/Resources/"   # localizations (the system language picks one)
cp Resources/AppIcon.icns "$APP/Contents/Resources/"  # from brand/lantern-icon.svg (brand/make-icons.sh)
/usr/bin/python3 ../integrations/alfred/build.py "$APP/Contents/Resources" >/dev/null  # Settings → Search from: Alfred

if (( STANDALONE )); then
  VENV_PY="$REPO/.venv/bin/python"
  [[ -x "$VENV_PY" ]] || { echo "error: $VENV_PY not found (run: uv sync)" >&2; exit 1; }
  PY_ROOT="${VENV_PY:A:h:h}"   # relocatable cpython-3.12 from uv (@executable_path/../lib/libpython3.12.dylib)
  PY_DEST="$APP/Contents/Resources/python"
  mkdir -p "$PY_DEST/bin" "$PY_DEST/lib"
  cp "$PY_ROOT/bin/python3.12" "$PY_DEST/bin/python3.12"
  ln -s python3.12 "$PY_DEST/bin/python3"
  ln -s python3.12 "$PY_DEST/bin/python"
  cp "$PY_ROOT/lib/libpython3.12.dylib" "$PY_DEST/lib/libpython3.12.dylib"
  rsync -a \
    --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='/test' --exclude='/idlelib' --exclude='/tkinter' \
    --exclude='/turtledemo' --exclude='/ensurepip' --exclude='/venv' \
    --exclude='/config-3.12-darwin/libpython3.12.a' --exclude='/site-packages' \
    "$PY_ROOT/lib/python3.12/" "$PY_DEST/lib/python3.12/"
  SITE_DEST="$PY_DEST/lib/python3.12/site-packages"
  mkdir -p "$SITE_DEST"
  rsync -a \
    --exclude='__pycache__' --exclude='*.pyc' \
    --exclude='/_lantern*' --exclude='/lantern-*' \
    --exclude='/pytest*' --exclude='/_pytest*' --exclude='/pluggy*' --exclude='/iniconfig*' --exclude='/py.py' \
    --exclude='/ruff*' --exclude='/PyObjCTest' \
    --exclude='/torch*' --exclude='/functorch*' --exclude='/nvfuser*' --exclude='/sympy*' --exclude='/mpmath*' --exclude='/networkx*' \
    --exclude='/cv2*' --exclude='/opencv*' --exclude='/fastapi*' --exclude='/uvicorn*' --exclude='/starlette*' --exclude='/websockets*' \
    --exclude='/multipart*' --exclude='/python_multipart*' --exclude='/mlx_audio*' --exclude='/llguidance*' --exclude='/miniaudio*' \
    --exclude='/sentencepiece*' --exclude='/sounddevice*' --exclude='/audiomentations*' --exclude='/librosa*' --exclude='/scikit_learn*' --exclude='/sklearn*' \
    "$REPO/.venv/lib/python3.12/site-packages/" "$SITE_DEST/"
  rsync -a --exclude='__pycache__' --exclude='*.pyc' "$REPO/daemon/lantern" "$SITE_DEST/"
fi

IDENTITY=""
if (( ! DMG )); then
  IDENTITY="$(security find-identity -v -p codesigning | grep -m1 'Apple Development' | sed -E 's/.*"(.*)"/\1/' || true)"
fi
SIGN_ID="${IDENTITY:--}"
if (( STANDALONE )); then
  find "$APP/Contents/Resources/python" -type f \( -name "*.so" -o -name "*.dylib" -o -name "python3.12" \) -print0 \
    | xargs -0 -n 1 -P 8 codesign --force --sign "$SIGN_ID"
fi
codesign --force --sign "$SIGN_ID" "$APP/Contents/MacOS/lantern-media"  # nested code first, then the bundle
codesign --force --sign "$SIGN_ID" "$APP"
if [[ -n "$IDENTITY" ]]; then
  echo "signed with: $IDENTITY"
else
  echo "signed ad-hoc (sign in to Xcode → Settings → Accounts for a stable signature; macOS permissions then survive rebuilds)"
fi
codesign --verify --deep --strict "$APP"
echo "built $APP"

if (( DMG )); then
  STAGE=build/dmg-stage
  DMG_OUT=build/Lantern.dmg
  rm -rf "$STAGE" "$DMG_OUT"
  mkdir -p "$STAGE"
  ditto "$APP" "$STAGE/Lantern.app"
  ln -s /Applications "$STAGE/Applications"
  hdiutil create -volname "Lantern" -srcfolder "$STAGE" -ov -format UDZO "$DMG_OUT" >/dev/null
  rm -rf "$STAGE"
  echo "built $DMG_OUT"
fi

# --install: copy to /Applications (launch at login and Spotlight work best from there); quits a running copy first.
if (( INSTALL )); then
  if pgrep -xq Lantern; then
    osascript -e 'tell application id "com.lantern.app" to quit' >/dev/null 2>&1 || pkill -x Lantern || true
    for i in {1..20}; do pgrep -xq Lantern || break; sleep 0.5; done
  fi
  rm -rf /Applications/Lantern.app
  ditto "$APP" /Applications/Lantern.app
  echo "installed /Applications/Lantern.app"
fi


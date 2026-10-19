#!/bin/bash
# One-command setup after `git clone`: system tools, Python dependencies, models, the `lantern` command and its
# manual page (links in ~/.local, no sudo), the menu bar app.
# Safe to run again (every step checks what is already there). Nothing runs in the background afterwards and the
# LaunchAgent is never installed here (the app starts the daemon itself; see the README for the optional agent).
#
#   ./install.sh             ask about the optional models and the app
#   ./install.sh --all       everything, no questions (Whisper, SigLIP 2, the app in /Applications)
#   ./install.sh --minimal   command line + the required model only, no questions
#   ./install.sh --uninstall remove what this script set up (links, PATH line, the app); keeps index and models
#   options: --no-app  --test (also run the fast test suite)  --help
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

MODE=ask APP=ask RUN_TESTS=0 UNINSTALL=0
for arg in "$@"; do
  case "$arg" in
    --all) MODE=all; [[ $APP == ask ]] && APP=yes ;;
    --minimal) MODE=minimal; APP=no ;;
    --no-app) APP=no ;;
    --test) RUN_TESTS=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done
[[ -t 0 ]] || { [[ $MODE == ask ]] && MODE=minimal; [[ $APP == ask ]] && APP=no; }  # no terminal: no questions

bold=$'\033[1m' dim=$'\033[2m' red=$'\033[31m' green=$'\033[32m' reset=$'\033[0m'
step() { printf '\n%s==> %s%s\n' "$bold" "$1" "$reset"; }
ok() { printf '  %s✓%s %s\n' "$green" "$reset" "$1"; }
note() { printf '  %s%s%s\n' "$dim" "$1" "$reset"; }
fail() { printf '\n%s✗ %s%s\n' "$red" "$1" "$reset" >&2; exit 1; }
ask() {  # ask "question" -> 0 for yes (default no)
  local reply
  read -r -p "  $1 [y/N] " reply
  [[ $reply == [yYtT]* ]]
}

# The command and its manual page are symlinks into this checkout, so `git pull` updates both. macOS `man` finds
# ~/.local/share/man because ~/.local/bin is on PATH (it derives the manual path from PATH).
BIN_DIR="$HOME/.local/bin" MAN_DIR="$HOME/.local/share/man/man1"
case "$(basename "${SHELL:-zsh}")" in bash) PROFILE="$HOME/.bash_profile" ;; *) PROFILE="$HOME/.zprofile" ;; esac
MARK_BEGIN="# >>> lantern >>>" MARK_END="# <<< lantern <<<"
on_path() { [[ ":$PATH:" == *":$BIN_DIR:"* ]]; }
link() {  # link <source> <destination>: never replaces a file that is not Lantern's own link
  if [[ -e $2 || -L $2 ]] && [[ "$(readlink "$2")" != "$1" ]]; then
    note "kept $2 (not Lantern's; move it away and run this script again)"
    return 1
  fi
  ln -sfn "$1" "$2"
}
unlink_ours() { [[ -L $1 && "$(readlink "$1")" == "$REPO/"* ]] && rm "$1" && ok "removed $1"; return 0; }

if (( UNINSTALL )); then
  step "Removing what install.sh set up"
  unlink_ours "$BIN_DIR/lantern"
  unlink_ours "$MAN_DIR/lantern.1"
  if [[ -f $PROFILE ]] && grep -qF "$MARK_BEGIN" "$PROFILE"; then
    sed -i '' "/$MARK_BEGIN/,/$MARK_END/d" "$PROFILE" && ok "removed the PATH line from $PROFILE"
  fi
  if [[ -d /Applications/Lantern.app ]] && [[ -t 0 ]] && ask "Remove /Applications/Lantern.app?"; then
    osascript -e 'tell application id "com.lantern.app" to quit' >/dev/null 2>&1 || true
    rm -rf /Applications/Lantern.app && ok "removed /Applications/Lantern.app"
  fi
  echo "  Left in place (remove by hand if you want):"
  [[ -f "$HOME/Library/LaunchAgents/com.lantern.daemon.plist" ]] && note "  LaunchAgent:  uv run lantern daemon uninstall"
  note "  index and settings:  rm -rf ~/Library/Application\ Support/Lantern"
  note "  models:  rm -rf ~/.cache/huggingface/hub/models--mlx-community--embeddinggemma-2-8bit (and the others)"
  note "  this folder, with .venv:  rm -rf \"$REPO\""
  exit 0
fi

step "Checking this Mac"
[[ $(uname -s) == Darwin ]] || fail "Lantern runs on macOS only."
[[ $(uname -m) == arm64 ]] || fail "Lantern needs Apple silicon (M1 or newer): the models run on MLX."
# macOS 26.2: MLX's macOS 26 wheels are built for 26.2 (their libraries' minimum), and the app is made for Liquid Glass.
macos_version="$(sw_vers -productVersion)"
macos_major="${macos_version%%.*}"; macos_rest="${macos_version#*.}"; macos_minor="${macos_rest%%.*}"
[[ "$macos_rest" == "$macos_version" ]] && macos_minor=0
(( macos_major > 26 || (macos_major == 26 && macos_minor >= 2) )) \
  || fail "macOS 26.2 (Tahoe) or newer is required (this Mac: $macos_version)."
ok "macOS $(sw_vers -productVersion), $(sysctl -n machdep.cpu.brand_string)"
xcode-select -p >/dev/null 2>&1 || fail "Command Line Tools are missing. Run: xcode-select --install  (then this script again)"
ok "Command Line Tools"

step "Homebrew, uv, ffmpeg (MKV/WebM; other formats are decoded by macOS)"
export HOMEBREW_NO_ANALYTICS=1 HOMEBREW_NO_ENV_HINTS=1  # only for this script's brew calls
if ! command -v brew >/dev/null 2>&1; then
  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
    note "brew is installed but not on your PATH; to fix that for new terminals:"
    note "  echo 'eval \"\$(/opt/homebrew/bin/brew shellenv)\"' >> ~/.zprofile"
  else
    echo "  Homebrew (https://brew.sh) is needed for uv and ffmpeg."
    if [[ -t 0 ]] && ask "Run the official Homebrew installer now?"; then
      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
      eval "$(/opt/homebrew/bin/brew shellenv)"
    else
      fail "Install Homebrew from https://brew.sh, then run ./install.sh again."
    fi
  fi
fi
ok "Homebrew $(brew --version | head -1 | awk '{print $2}')"
missing=()
for tool in uv ffmpeg; do command -v "$tool" >/dev/null 2>&1 || missing+=("$tool"); done
if (( ${#missing[@]} )); then
  brew install "${missing[@]}"
fi
ok "uv $(uv --version | awk '{print $2}'), ffmpeg $(ffmpeg -version | head -1 | awk '{print $3}')"

step "Python dependencies (uv sync: Python 3.12 and packages into .venv)"
export UV_PYTHON_PREFERENCE=only-managed  # python.org builds lack sqlite enable_load_extension; uv's managed build has it
uv sync --quiet
ok ".venv ready"

step "Models (one-time download into ~/.cache/huggingface/hub)"
# Repositories and pinned revisions come from the code, so this script never drifts from what Lantern loads.
models="$(uv run --quiet python - <<'EOF'
from lantern.embedding import mlx_backend, siglip
from lantern.transcription import whisper
print("required", mlx_backend.REPO, mlx_backend.REVISION, "EmbeddingGemma 2 (search, ~1.2 GB)")
print("whisper", whisper.REPO, whisper.REVISION, "Whisper large-v3-turbo (transcripts of recordings, ~1.6 GB)")
print("photos", siglip.IMAGE_REPO, siglip.IMAGE_REVISION, "SigLIP 2 image side (better photo search, ~0.8 GB)")
print("photos", siglip.TEXT_REPO, siglip.TEXT_REVISION, "SigLIP 2 text side (~0.2 GB)")
EOF
)"
hub="${HF_HOME:-$HOME/.cache/huggingface}/hub"
want_whisper=0 want_photos=0
case $MODE in
  all) want_whisper=1 want_photos=1 ;;
  ask)
    ask "Download Whisper for searching inside recordings and videos (~1.6 GB)?" && want_whisper=1
    ask "Download SigLIP 2 for better photo search (~1 GB; without it photos are still searchable)?" && want_photos=1
    ;;
esac
while read -r group repo revision label; do
  case $group in
    whisper) (( want_whisper )) || { note "skipped: $label"; continue; } ;;
    photos) (( want_photos )) || { note "skipped: $label"; continue; } ;;
  esac
  if [[ -d "$hub/models--${repo//\//--}/snapshots/$revision" ]]; then
    ok "$label (already downloaded)"
  else
    echo "  downloading $label"
    uv run --quiet hf download "$repo" --revision "$revision" >/dev/null
    ok "$label"
  fi
done <<< "$models"
(( want_whisper )) || note "without Whisper, recordings are found by sound only; turn transcripts off with: uv run lantern config transcribe false"

step "Checking that search works offline"
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 uv run --quiet python - <<'EOF'
from lantern.embedding.base import format_document, format_query
from lantern.service.worker import make_embedder

e = make_embedder()
q, a, b = e.embed_text([format_query("invoice for electricity"), format_document(None, "faktura za prąd, październik"),
                        format_document(None, "przepis na sernik")])
assert float(q @ a) > float(q @ b), "the model loaded but ranks nonsense"
EOF
ok "EmbeddingGemma 2 loads from disk and ranks correctly (no network)"
if (( RUN_TESTS )); then
  uv run pytest -q && ok "fast tests"
fi

step "The lantern command and its manual page"
mkdir -p "$BIN_DIR" "$MAN_DIR"
if link "$REPO/.venv/bin/lantern" "$BIN_DIR/lantern"; then
  "$BIN_DIR/lantern" --version >/dev/null && ok "$BIN_DIR/lantern"
fi
if link "$REPO/docs/lantern.1" "$MAN_DIR/lantern.1"; then
  ok "$MAN_DIR/lantern.1  (man lantern)"
fi
if on_path; then
  ok "$BIN_DIR is on your PATH"
elif [[ -f $PROFILE ]] && grep -qF "$MARK_BEGIN" "$PROFILE"; then
  note "$PROFILE already adds $BIN_DIR to PATH: open a new terminal window"
else
  line="export PATH=\"\$HOME/.local/bin:\$PATH\""
  if [[ $MODE == all ]] || { [[ $MODE == ask ]] && ask "Add $BIN_DIR to your PATH in $PROFILE (so \`lantern\` and \`man lantern\` work)?"; }; then
    printf '\n%s\n%s\n%s\n' "$MARK_BEGIN" "$line" "$MARK_END" >> "$PROFILE"
    ok "added to $PROFILE (new terminal windows; remove with ./install.sh --uninstall)"
  else
    note "to use \`lantern\` and \`man lantern\` directly, add this line to $PROFILE:"
    note "  $line"
  fi
fi

step "Menu bar app"
xcode="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"
if [[ $APP == no ]]; then
  note "skipped (build later with: app/build.sh --install)"
elif [[ ! -d $xcode ]] || ! DEVELOPER_DIR="$xcode" xcrun --find swift >/dev/null 2>&1; then
  note "Xcode 26 not found: the app is skipped (Command Line Tools cannot build it). The command line works."
  note "After installing Xcode from the App Store: app/build.sh --install"
elif [[ $APP == yes ]] || ask "Build Lantern.app and copy it to /Applications?"; then
  app/build.sh --install
  ok "/Applications/Lantern.app"
else
  note "skipped (build later with: app/build.sh --install)"
fi

step "Done"
if [[ -d /Applications/Lantern.app ]]; then
  echo "  Open Lantern:   open /Applications/Lantern.app   (then ⌥Space to search)"
fi
echo "  Command line:   lantern index ~/Documents && lantern search \"your query\""
echo "  Manual:         man lantern   (full reference: docs/CLI.md)"
on_path || note "in this terminal window, until you open a new one: $BIN_DIR/lantern …"
note "Optional, not done by this script: start the daemon at login without the app: lantern daemon install"

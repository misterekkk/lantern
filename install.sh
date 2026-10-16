#!/bin/bash
# One-command setup after `git clone`: system tools, Python dependencies, models, the menu bar app.
# Safe to run again (every step checks what is already there). Nothing runs in the background afterwards and the
# LaunchAgent is never installed here (the app starts the daemon itself; see the README for the optional agent).
#
#   ./install.sh             ask about the optional models and the app
#   ./install.sh --all       everything, no questions (Whisper, SigLIP 2, the app in /Applications)
#   ./install.sh --minimal   command line + the required model only, no questions
#   options: --no-app  --test (also run the fast test suite)  --help
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

MODE=ask APP=ask RUN_TESTS=0
for arg in "$@"; do
  case "$arg" in
    --all) MODE=all; [[ $APP == ask ]] && APP=yes ;;
    --minimal) MODE=minimal; APP=no ;;
    --no-app) APP=no ;;
    --test) RUN_TESTS=1 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

step "Checking this Mac"
[[ $(uname -s) == Darwin ]] || fail "Lantern runs on macOS only."
[[ $(uname -m) == arm64 ]] || fail "Lantern needs Apple silicon (M1 or newer): the models run on MLX."
macos_major="$(sw_vers -productVersion | cut -d. -f1)"
(( macos_major >= 14 )) || fail "macOS 14 or newer is required (this Mac: $(sw_vers -productVersion))."
ok "macOS $(sw_vers -productVersion), $(sysctl -n machdep.cpu.brand_string)"
xcode-select -p >/dev/null 2>&1 || fail "Command Line Tools are missing. Run: xcode-select --install  (then this script again)"
ok "Command Line Tools"

step "Homebrew, uv, ffmpeg"
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
uv run --quiet lantern --help >/dev/null && ok "lantern command"
if (( RUN_TESTS )); then
  uv run pytest -q && ok "fast tests"
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
echo "  Command line:   uv run lantern index ~/Documents && uv run lantern search \"your query\""
note "Optional, not done by this script: start the daemon at login without the app: uv run lantern daemon install"

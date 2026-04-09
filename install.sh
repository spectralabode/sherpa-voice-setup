#!/usr/bin/env bash
# =============================================================================
# sherpa-voice installer -- offline TTS / STT web UI via sherpa-onnx
#
# Usage:
#   bash install.sh
#   bash install.sh -i /opt/sherpa-voice
#   bash install.sh --install-dir=/opt/sherpa-voice
#   bash install.sh --test              # run TTS+STT smoke test after install
#   SHERPA_INSTALL_DIR=/opt/sherpa-voice bash install.sh
#   SHERPA_PORT=12345 bash install.sh
#   GITHUB_TOKEN=ghp_xxx bash install.sh   # avoids GitHub API rate limits
# =============================================================================
set -euo pipefail

INSTALL_DIR="${SHERPA_INSTALL_DIR:-$HOME/sherpa-voice}"
PORT="${SHERPA_PORT:-45678}"
PYTHON="${PYTHON:-python3}"
RUN_TEST=false

# ---- parse CLI args ----------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        -i)           INSTALL_DIR="$2"; shift 2 ;;
        -i*)          INSTALL_DIR="${1#-i}"; shift ;;
        --install-dir=*) INSTALL_DIR="${1#*=}"; shift ;;
        --install-dir)   INSTALL_DIR="$2"; shift 2 ;;
        --port=*)     PORT="${1#*=}"; shift ;;
        --port|-p)    PORT="$2"; shift 2 ;;
        --test)       RUN_TEST=true; shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ---- colours -----------------------------------------------------------------
R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'; B='\033[1;34m'; NC='\033[0m'
info()  { echo -e "${B}[info]${NC}  $*" >&2; }
ok()    { echo -e "${G}[ok]${NC}    $*" >&2; }
warn()  { echo -e "${Y}[warn]${NC}  $*" >&2; }
err()   { echo -e "${R}[err]${NC}   $*" >&2; exit 1; }
step()  { echo -e "\n${B}==  $*${NC}" >&2; }

echo -e "${B}"
cat <<'BANNER'
 ________  ___  ___  _______   ________  ________  ________
|\   ____\|\  \|\  \|\  ___ \ |\   __  \|\   __  \|\   __  \
\ \  \___|\ \  \\\  \ \   __/|\ \  \|\  \ \  \|\  \ \  \|\  \
 \ \_____  \ \   __  \ \  \_|/_\ \   _  _\ \   ____\ \   __  \
  \|____|\  \ \  \ \  \ \  \_|\ \ \  \\  \\ \  \___|\ \  \ \  \
    ____\_\  \ \__\ \__\ \_______\ \__\\ _\\ \__\    \ \__\ \__\
   |\_________\|__|\|__|\|_______|\|__|\|__|\|__|     \|__|\|__|
   \|_________|
        offline voice AI installer (TTS + STT)
BANNER
echo -e "${NC}"
info "Install dir : $INSTALL_DIR"
info "Port        : $PORT"
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    info "GitHub token: set (authenticated)"
else
    warn "GITHUB_TOKEN not set -- unauthenticated API (60 req/hr limit)"
    warn "Tip: GITHUB_TOKEN=ghp_xxx bash install.sh"
fi
echo

# ---- prerequisites -----------------------------------------------------------
step "Checking prerequisites"

need_cmd() { command -v "$1" &>/dev/null || err "$1 is required but not found in PATH."; }
need_cmd curl
need_cmd "$PYTHON"

if ! command -v ffmpeg &>/dev/null; then
    warn "ffmpeg not found -- attempting install..."
    if   command -v apt-get &>/dev/null; then sudo apt-get install -y ffmpeg
    elif command -v dnf     &>/dev/null; then sudo dnf install -y ffmpeg
    elif command -v pacman  &>/dev/null; then sudo pacman -S --noconfirm ffmpeg
    elif command -v brew    &>/dev/null; then brew install ffmpeg
    else err "ffmpeg is required but could not be auto-installed. Please install it manually."
    fi
fi

PYVER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python $PYVER"
[[ "${PYVER%%.*}" -ge 3 && "${PYVER#*.}" -ge 9 ]] || err "Python 3.9+ required (found $PYVER)"

#if ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
if ! ldconfig -p 2>/dev/null | grep -Eq 'libportaudio(\.so)?(\.[0-9]+)?'; then
    warn "libportaudio2 not found -- attempting install..."
    if   command -v apt-get &>/dev/null; then sudo apt-get install -y libportaudio2
    elif command -v dnf     &>/dev/null; then sudo dnf install -y portaudio
    elif command -v pacman  &>/dev/null; then sudo pacman -S --noconfirm portaudio
    elif command -v brew    &>/dev/null; then brew install portaudio
    else warn "Cannot auto-install libportaudio2 -- mic recording may not work"
    fi
fi

ok "Prerequisites OK"

# ---- GitHub API helpers ------------------------------------------------------
gh_api() {
    # gh_api <url> -- print JSON to stdout, empty on error
    local url=$1
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        curl -fsSL \
            -H "Authorization: Bearer ${GITHUB_TOKEN}" \
            -H "Accept: application/vnd.github+json" \
            "$url" 2>/dev/null || true
    else
        curl -fsSL \
            -H "Accept: application/vnd.github+json" \
            "$url" 2>/dev/null || true
    fi
}

fetch_assets() {
    # fetch_assets <owner/repo> <tag> <cache_file>
    # writes name<TAB>url lines to cache_file; returns 1 on failure
    local repo=$1 tag=$2 cache=$3
    local json
    json=$(gh_api "https://api.github.com/repos/${repo}/releases/tags/${tag}")
    if [[ -z "$json" ]] || echo "$json" | grep -qE '"API rate limit|"Bad credentials'; then
        return 1
    fi
    echo "$json" | "$PYTHON" -c "
import json, sys
for a in json.load(sys.stdin).get('assets', []):
    print(a['name'] + '\t' + a['browser_download_url'])
" > "$cache"
    info "  $(wc -l < "$cache" | tr -d ' ') assets fetched for $tag"
    return 0
}

find_url() {
    # find_url <cache_file> <regex> -- print download URL of latest matching asset
    local cache=$1 pattern=$2
    grep -E "^${pattern}" "$cache" 2>/dev/null \
        | sort -r | head -1 | cut -f2 || true
}

# ---- python venv + packages --------------------------------------------------
step "Setting up Python venv"
mkdir -p "$INSTALL_DIR"/{venv,bin}
VENV="$INSTALL_DIR/venv"
if [[ ! -f "$VENV/bin/python" ]]; then
    "$PYTHON" -m venv "$VENV"
    ok "venv created"
else
    ok "venv already exists"
fi

PIP="$VENV/bin/pip"
VPYTHON="$VENV/bin/python"

info "Installing Python packages..."
"$PIP" install -q --upgrade pip
"$PIP" install -q sherpa-onnx sounddevice numpy fastapi "uvicorn[standard]" python-multipart trafilatura readability-lxml

SHERPA_VER=$("$VPYTHON" -c "import sherpa_onnx; print(sherpa_onnx.__version__)" 2>/dev/null)
ok "sherpa-onnx $SHERPA_VER installed"

# ---- resolve model URLs from GitHub API (with fallbacks) ---------------------
step "Resolving latest model URLs"
mkdir -p "$INSTALL_DIR/models"
M="$INSTALL_DIR/models"

TTS_CACHE=$(mktemp); ASR_CACHE=$(mktemp)
trap 'rm -f "$TTS_CACHE" "$ASR_CACHE"' EXIT

TTS_OK=false; ASR_OK=false
fetch_assets "k2-fsa/sherpa-onnx" "tts-models" "$TTS_CACHE" && TTS_OK=true \
    || warn "GitHub API failed for tts-models -- using fallback URLs"
fetch_assets "k2-fsa/sherpa-onnx" "asr-models" "$ASR_CACHE" && ASR_OK=true \
    || warn "GitHub API failed for asr-models -- using fallback URLs"

resolve_tts() {
    local pattern=$1 fallback=$2 url=""
    $TTS_OK && url=$(find_url "$TTS_CACHE" "$pattern") || true
    if [[ -z "$url" ]]; then
        url="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/$fallback"
    fi
    echo "$url"
}

resolve_asr() {
    local pattern=$1 fallback=$2 url=""
    $ASR_OK && url=$(find_url "$ASR_CACHE" "$pattern") || true
    if [[ -z "$url" ]]; then
        url="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$fallback"
    fi
    echo "$url"
}

resolve_silero() {
    local json tag
    json=$(gh_api "https://api.github.com/repos/snakers4/silero-vad/releases/latest")
    if [[ -n "$json" ]] && ! echo "$json" | grep -q '"message"'; then
        tag=$(echo "$json" | "$PYTHON" -c "import json,sys; print(json.load(sys.stdin)['tag_name'])")
        info "  Silero VAD latest tag: $tag"
        echo "https://github.com/snakers4/silero-vad/raw/${tag}/src/silero_vad/data/silero_vad.onnx"
    else
        warn "Silero VAD: using fallback tag v5.1"
        echo "https://github.com/snakers4/silero-vad/raw/v5.1/src/silero_vad/data/silero_vad.onnx"
    fi
}

URL_ESPEAK=$(   resolve_tts "espeak-ng-data\\.tar\\.bz2"                               "espeak-ng-data.tar.bz2")
URL_LESSAC=$(   resolve_tts "vits-piper-en_US-lessac-medium\\.tar\\.bz2"               "vits-piper-en_US-lessac-medium.tar.bz2")
URL_AMY=$(      resolve_tts "vits-piper-en_US-amy-medium-int8\\.tar\\.bz2"             "vits-piper-en_US-amy-medium-int8.tar.bz2")
URL_KOKORO=$(   resolve_tts "kokoro-int8-en-v.*\\.tar\\.bz2"                           "kokoro-int8-en-v0_19.tar.bz2")
URL_KITTEN=$(   resolve_tts "kitten-nano-en-v.*-fp16\\.tar\\.bz2"                      "kitten-nano-en-v0_2-fp16.tar.bz2")
URL_ZIPFORMER=$(resolve_asr "sherpa-onnx-streaming-zipformer-en-20M-[^-]*\\.tar\\.bz2" "sherpa-onnx-streaming-zipformer-en-20M-2023-02-17.tar.bz2")
URL_SILERO=$(   resolve_silero)

info "espeak    : $(basename "$URL_ESPEAK")"
info "lessac    : $(basename "$URL_LESSAC")"
info "amy       : $(basename "$URL_AMY")"
info "kokoro    : $(basename "$URL_KOKORO")"
info "kitten    : $(basename "$URL_KITTEN")"
info "zipformer : $(basename "$URL_ZIPFORMER")"
info "silero    : $(basename "$URL_SILERO")"

# ---- helpers -----------------------------------------------------------------
download() {
    local url=$1 dest=$2
    [[ -s "$dest" ]] && { ok "skip (exists): $(basename "$dest")"; return; }
    info "Downloading $(basename "$dest")..."
    curl -fsSL --retry 3 --progress-bar -o "${dest}.tmp" "$url"
    mv "${dest}.tmp" "$dest"
    ok "$(basename "$dest") ($(du -sh "$dest" | cut -f1))"
}

extract() {
    local archive=$1 dest=$2 strip=${3:-}
    [[ -d "$dest" ]] && { ok "skip (exists): $(basename "$dest")"; return; }
    info "Extracting $(basename "$archive")..."
    mkdir -p "$dest"
    if [[ "$strip" == "--strip" ]]; then
        tar xjf "$archive" -C "$dest" --strip-components=1
    else
        tar xjf "$archive" -C "$(dirname "$dest")"
    fi
    ok "-> $(basename "$dest")"
}

dir_of() { basename "$1" .tar.bz2; }

# ---- download & extract all models -------------------------------------------
step "Downloading models"

download "$URL_ESPEAK" "$M/espeak-ng-data.tar.bz2"
extract  "$M/espeak-ng-data.tar.bz2" "$M/espeak-ng-data" --strip

LESSAC_DIR="$M/$(dir_of "$URL_LESSAC")"
download "$URL_LESSAC" "$M/piper-lessac.tar.bz2"
extract  "$M/piper-lessac.tar.bz2" "$LESSAC_DIR"

AMY_DIR="$M/$(dir_of "$URL_AMY")"
download "$URL_AMY" "$M/piper-amy.tar.bz2"
extract  "$M/piper-amy.tar.bz2" "$AMY_DIR"

KOKORO_DIR="$M/$(dir_of "$URL_KOKORO")"
download "$URL_KOKORO" "$M/kokoro-en.tar.bz2"
extract  "$M/kokoro-en.tar.bz2" "$KOKORO_DIR"

KITTEN_DIR="$M/$(dir_of "$URL_KITTEN")"
download "$URL_KITTEN" "$M/kitten-nano.tar.bz2"
extract  "$M/kitten-nano.tar.bz2" "$KITTEN_DIR"

ZIPFORMER_DIR="$M/$(dir_of "$URL_ZIPFORMER")"
download "$URL_ZIPFORMER" "$M/zipformer.tar.bz2"
extract  "$M/zipformer.tar.bz2" "$ZIPFORMER_DIR"

download "$URL_SILERO" "$M/silero_vad.onnx"

ok "All models ready -- $(du -sh "$M" | cut -f1) total"

# ---- copy app files ----------------------------------------------------------
step "Copying app files"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for f in app.py ui.html ui-enhanced.html run.sh tts tts-cli.py stt stt-cli.py read read-cli.py configure configure.py config.py test.sh README.md; do
    [[ -f "$SCRIPT_DIR/$f" ]] || err "$f not found next to install.sh -- make sure all 13 files are together"
    cp "$SCRIPT_DIR/$f" "$INSTALL_DIR/$f"
    ok "copied $f"
done
chmod +x "$INSTALL_DIR/run.sh" "$INSTALL_DIR/tts" "$INSTALL_DIR/stt" "$INSTALL_DIR/read" "$INSTALL_DIR/configure" "$INSTALL_DIR/test.sh"

# ---- optional smoke test -----------------------------------------------------
if $RUN_TEST; then
    bash "$INSTALL_DIR/test.sh" -i "$INSTALL_DIR"
fi

# ---- done --------------------------------------------------------------------
echo
echo -e "${G}=================================================${NC}"
echo -e "${G}  Installation complete!${NC}"
echo -e "${G}=================================================${NC}"
echo
echo "  Start  : bash $INSTALL_DIR/run.sh"
echo "  Open   : http://localhost:$PORT"
echo "  TTS    : $INSTALL_DIR/tts \"Hello world\" | aplay"
echo "  STT    : $INSTALL_DIR/stt --mic"
echo "  Config : $INSTALL_DIR/configure"
echo "  Test   : bash $INSTALL_DIR/test.sh"
echo
echo "  Custom port : SHERPA_PORT=12345 bash $INSTALL_DIR/run.sh"
echo

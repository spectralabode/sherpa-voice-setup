#!/usr/bin/env bash
# =============================================================================
# sherpa-voice smoke test -- run against an existing install
#
# Usage:
#   bash test.sh                        # assumes ~/sherpa-voice
#   bash test.sh -i /opt/sherpa-voice
#   bash test.sh --install-dir=/opt/sherpa-voice
# =============================================================================
set -euo pipefail

INSTALL_DIR="${SHERPA_INSTALL_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -i)              INSTALL_DIR="$2"; shift 2 ;;
        -i*)             INSTALL_DIR="${1#-i}"; shift ;;
        --install-dir=*) INSTALL_DIR="${1#*=}"; shift ;;
        --install-dir)   INSTALL_DIR="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

R='\033[0;31m'; G='\033[0;32m'; Y='\033[1;33m'; B='\033[1;34m'; NC='\033[0m'
ok()   { echo -e "${G}[ok]${NC}    $*"; }
fail() { echo -e "${R}[fail]${NC}  $*"; FAILED=true; }
info() { echo -e "${B}[info]${NC}  $*"; }
step() { echo -e "\n${B}==  $*${NC}"; }

FAILED=false

echo -e "${B}sherpa-voice smoke test${NC}"
info "Install dir: $INSTALL_DIR"

# ---- sanity checks -----------------------------------------------------------
step "Checking install"

for f in tts stt run.sh app.py tts-cli.py stt-cli.py config.py configure.py venv/bin/python; do
    if [[ -e "$INSTALL_DIR/$f" ]]; then
        ok "$f"
    else
        fail "$f missing"
    fi
done

for d in models/espeak-ng-data models/vits-piper-en_US-lessac-medium \
          models/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17; do
    if [[ -d "$INSTALL_DIR/$d" ]]; then
        ok "$d"
    else
        fail "$d missing"
    fi
done

$FAILED && { echo -e "\n${R}Install looks incomplete — re-run install.sh${NC}"; exit 1; }

# ---- TTS test ----------------------------------------------------------------
step "TTS test"

TEST_WAV=$(mktemp --suffix=.wav)
trap 'rm -f "$TEST_WAV"' EXIT

TEST_PHRASE="the quick brown fox jumps over the lazy dog"
info "Voice: piper-lessac | Text: \"$TEST_PHRASE\""

if "$INSTALL_DIR/tts" "$TEST_PHRASE" -v piper-lessac -o "$TEST_WAV" 2>/dev/null; then
    SIZE=$(du -sh "$TEST_WAV" | cut -f1)
    ok "WAV generated ($SIZE)"
else
    fail "TTS failed"
    FAILED=true
fi

# ---- STT test ----------------------------------------------------------------
step "STT test"

if ! $FAILED; then
    info "Transcribing generated WAV..."
    TRANSCRIPT=$("$INSTALL_DIR/stt" "$TEST_WAV" 2>/dev/null | tr '[:upper:]' '[:lower:]')
    ok "Transcript: \"$TRANSCRIPT\""

    MISSING=""
    for word in quick brown fox lazy dog; do
        echo "$TRANSCRIPT" | grep -qw "$word" || MISSING="$MISSING $word"
    done

    if [[ -z "$MISSING" ]]; then
        ok "All expected words found"
    else
        fail "Missing words:$MISSING"
        FAILED=true
    fi
fi

# ---- web server test ---------------------------------------------------------
step "Web server test"

SHERPA_PORT=45679  # use a non-default port to avoid conflicts
export SHERPA_PORT

nohup "$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/app.py" \
    > /tmp/sherpa-test-server.log 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null; rm -f "$TEST_WAV"' EXIT

info "Waiting for server (pid $SERVER_PID)..."
for i in $(seq 1 15); do
    sleep 1
    if curl -sf "http://localhost:$SHERPA_PORT/voices" > /dev/null 2>&1; then
        break
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        fail "Server exited early -- check /tmp/sherpa-test-server.log"
        FAILED=true
        break
    fi
done

BASE="http://localhost:$SHERPA_PORT"
SERVER_UP=false

if kill -0 "$SERVER_PID" 2>/dev/null && curl -sf "$BASE/voices" > /dev/null 2>&1; then
    SERVER_UP=true
    VOICES=$(curl -sf "$BASE/voices" | grep -o '"label"' | wc -l | tr -d ' ')
    ok "Server responded — $VOICES voices available"
else
    fail "Server did not respond on port $SHERPA_PORT"
    FAILED=true
fi

if $SERVER_UP; then
    # web TTS
    WEB_WAV=$(mktemp --suffix=.wav)
    info "Web TTS: POST /tts ..."
    HTTP=$(curl -sf -o "$WEB_WAV" -w "%{http_code}" \
        -F "text=$TEST_PHRASE" \
        -F "voice=piper-lessac" \
        -F "speaker=0" \
        "$BASE/tts" 2>/dev/null)
    if [[ "$HTTP" == "200" ]] && [[ -s "$WEB_WAV" ]]; then
        ok "Web TTS: got WAV ($(du -sh "$WEB_WAV" | cut -f1), HTTP $HTTP)"
    else
        fail "Web TTS: unexpected response (HTTP $HTTP)"
        FAILED=true
    fi

    # web STT — upload the WAV we just got back from TTS
    info "Web STT: POST /stt ..."
    STT_JSON=$(curl -sf -w "\n%{http_code}" \
        -F "file=@${WEB_WAV};type=audio/wav" \
        "$BASE/stt" 2>/dev/null)
    HTTP=$(echo "$STT_JSON" | tail -1)
    TRANSCRIPT=$(echo "$STT_JSON" | head -1 | grep -o '"transcript":"[^"]*"' | cut -d'"' -f4 | tr '[:upper:]' '[:lower:]')
    if [[ "$HTTP" == "200" ]] && [[ -n "$TRANSCRIPT" ]]; then
        ok "Web STT: \"$TRANSCRIPT\" (HTTP $HTTP)"
        MISSING=""
        for word in quick brown fox lazy dog; do
            echo "$TRANSCRIPT" | grep -qw "$word" || MISSING="$MISSING $word"
        done
        [[ -z "$MISSING" ]] && ok "Web STT: expected words found" \
                             || { fail "Web STT: missing words:$MISSING"; FAILED=true; }
    else
        fail "Web STT: unexpected response (HTTP $HTTP, body: $(echo "$STT_JSON" | head -1))"
        FAILED=true
    fi
    rm -f "$WEB_WAV"
fi

# ---- summary -----------------------------------------------------------------
echo
if $FAILED; then
    echo -e "${R}=================================================${NC}"
    echo -e "${R}  Smoke test FAILED — see messages above${NC}"
    echo -e "${R}=================================================${NC}"
    exit 1
else
    echo -e "${G}=================================================${NC}"
    echo -e "${G}  All tests passed!${NC}"
    echo -e "${G}=================================================${NC}"
fi

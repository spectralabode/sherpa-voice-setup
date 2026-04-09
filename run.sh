#!/usr/bin/env bash
# Usage:
#   bash run.sh
#   bash run.sh -p 12345
#   bash run.sh --port=12345
#   SHERPA_PORT=12345 bash run.sh
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export SHERPA_PORT="${SHERPA_PORT:-45678}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -p)        SHERPA_PORT="$2"; shift 2 ;;
        -p*)       SHERPA_PORT="${1#-p}"; shift ;;
        --port=*)  SHERPA_PORT="${1#*=}"; shift ;;
        --port)    SHERPA_PORT="$2"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

echo "Starting sherpa-voice on http://0.0.0.0:${SHERPA_PORT}"
exec "$DIR/venv/bin/python" "$DIR/app.py"

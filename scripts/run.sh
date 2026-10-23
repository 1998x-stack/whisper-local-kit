#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
PYTHON="$(resolve_python)"
export PATH="$(dirname "$PYTHON"):$PATH"
PORT="${WHISPER_PORT:-7865}"
HOST="${WHISPER_HOST:-127.0.0.1}"
[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1024 && PORT <= 65535 )) || { echo 'WHISPER_PORT 必须是 1024–65535' >&2; exit 2; }
[[ -n "$HOST" && "$HOST" != 0.0.0.0 && "$HOST" != :: ]] || { echo 'WHISPER_HOST 必须是指定网卡地址，不能监听全部网卡' >&2; exit 2; }
export OMP_NUM_THREADS="${WHISPER_THREADS:-2}"
umask 077
cd "$PROJECT_ROOT"
echo "Whisper Local http://$HOST:$PORT"
exec "$PYTHON" -m uvicorn app.main:app --host "$HOST" --port "$PORT" --workers 1

#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
ENV_NAME="${WHISPER_CONDA_ENV:-whisper-cpu}"
PORT="${WHISPER_PORT:-7865}"
export OMP_NUM_THREADS="${WHISPER_THREADS:-2}"
echo "Whisper Local: http://127.0.0.1:$PORT (仅本机访问，没有 API Key)"
exec conda run --no-capture-output -n "$ENV_NAME" python -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --workers 1

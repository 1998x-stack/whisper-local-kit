#!/usr/bin/env bash
set +e
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
PYTHON="$(resolve_python)" || exit 1
export PATH="$(dirname "$PYTHON"):$PATH"
echo '===== Python ====='; "$PYTHON" --version
echo '===== Dependencies ====='
"$PYTHON" -c 'import torch,whisper,fastapi; print("torch:",torch.__version__,"cuda:",torch.cuda.is_available())'
echo '===== FFmpeg ====='; ffmpeg -version | head -1; ffprobe -version | head -1
echo '===== Batch roots ====='; "$PYTHON" -c 'from app.extensions import allowed_roots; print(*allowed_roots(),sep="\n")'

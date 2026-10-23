#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
ENV_NAME="${WHISPER_CONDA_ENV:-whisper-cpu}"
if (( $# == 0 )); then set -- tiny; fi
for model in "$@"; do
  case "$model" in tiny|base|small) ;; *) echo "仅支持 tiny/base/small" >&2; exit 2 ;; esac
  echo "下载并校验模型：$model（仅首次需要联网）"
  conda run --no-capture-output -n "$ENV_NAME" python -c 'import os, sys, whisper; from pathlib import Path; path = Path(os.getenv("WHISPER_MODEL_DIR", str(Path.home()/".cache/whisper"))).expanduser(); path.mkdir(parents=True, exist_ok=True); whisper.load_model(sys.argv[1], device="cpu", download_root=str(path)); print("模型准备完成:",sys.argv[1])' "$model"
done

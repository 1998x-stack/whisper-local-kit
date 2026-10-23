#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
PYTHON="$(resolve_python)"
export PATH="$(dirname "$PYTHON"):$PATH"
if (( $# == 0 )); then set -- tiny; fi
for model in "$@"; do
  case "$model" in tiny|base|small) ;; *) echo "仅支持 tiny/base/small" >&2; exit 2 ;; esac
  "$PYTHON" -c 'import os,sys,whisper; from pathlib import Path; p=Path(os.getenv("WHISPER_MODEL_DIR",str(Path.home()/".cache/whisper"))).expanduser(); p.mkdir(parents=True,exist_ok=True); whisper.load_model(sys.argv[1],device="cpu",download_root=str(p)); print("模型已下载",sys.argv[1])' "$model"
done

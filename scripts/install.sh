#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
ENV_NAME="${WHISPER_CONDA_ENV:-whisper-cpu}"
CHANNEL="https://conda.anaconda.org/conda-forge"
if ! command -v conda >/dev/null 2>&1; then
  echo "ERROR: 找不到 conda。请先安装并初始化 Miniconda。" >&2; exit 1
fi
if ! conda run -n "$ENV_NAME" python -c 'import sys; assert sys.version_info >= (3, 10)' >/dev/null 2>&1; then
  echo "创建 Python 3.11 环境 $ENV_NAME（官方 conda-forge，不使用清华镜像）..."
  conda create -n "$ENV_NAME" --override-channels -c "$CHANNEL" python=3.11 pip ffmpeg -y
fi
if ! conda run -n "$ENV_NAME" python -m pip --version >/dev/null 2>&1; then
  echo "Conda 环境缺少 pip，正在补装..."
  conda install -n "$ENV_NAME" --override-channels -c "$CHANNEL" pip -y
fi
if ! conda run -n "$ENV_NAME" ffmpeg -version >/dev/null 2>&1 || ! conda run -n "$ENV_NAME" ffprobe -version >/dev/null 2>&1; then
  echo "正在安装 FFmpeg / ffprobe..."
  conda install -n "$ENV_NAME" --override-channels -c "$CHANNEL" ffmpeg -y
fi
if ! conda run -n "$ENV_NAME" python -c 'import torch' >/dev/null 2>&1; then
  echo "安装官方 PyTorch 2.7.1 CPU wheel（可能较大）..."
  conda run --no-capture-output -n "$ENV_NAME" python -m pip install 'torch==2.7.1' --index-url https://download.pytorch.org/whl/cpu
fi
conda run --no-capture-output -n "$ENV_NAME" python -m pip install -r requirements.txt -i https://pypi.org/simple
conda run --no-capture-output -n "$ENV_NAME" python -m pip check
conda run -n "$ENV_NAME" python -c 'import whisper, torch, fastapi, uvicorn; print("Whisper 可用 | PyTorch:", torch.__version__, "| CUDA:", torch.cuda.is_available())'
conda run -n "$ENV_NAME" ffmpeg -version | head -1
printf '\n安装完成。\n  下载 tiny 模型：bash scripts/prefetch-models.sh tiny\n  前台启动：bash scripts/run.sh\n  浏览器访问：http://127.0.0.1:7865\n'

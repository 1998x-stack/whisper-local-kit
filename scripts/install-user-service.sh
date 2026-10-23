#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
ENV_NAME="${WHISPER_CONDA_ENV:-whisper-cpu}"
PYTHON_PATH="$(conda run -n "$ENV_NAME" python -c 'import sys; print(sys.executable)' | tail -1)"
if [[ ! -x "$PYTHON_PATH" ]]; then echo "Conda 环境 Python 不存在: $PYTHON_PATH" >&2; exit 1; fi
if [[ "$PROJECT_ROOT" == *" "* ]]; then echo "项目路径含空格，请移到 ~/whisper-local 再执行。" >&2; exit 1; fi
CONDA_BIN="$(dirname "$PYTHON_PATH")"
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/whisper-local.service" <<UNIT
[Unit]
Description=Whisper Local CPU Web UI (loopback only)

[Service]
Type=simple
WorkingDirectory=$PROJECT_ROOT
Environment="PATH=$CONDA_BIN:/usr/local/bin:/usr/bin:/bin"
Environment=PYTHONUNBUFFERED=1
Environment=OMP_NUM_THREADS=2
Environment=WHISPER_THREADS=2
ExecStart=$PYTHON_PATH -m uvicorn app.main:app --host 127.0.0.1 --port 7865 --workers 1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable whisper-local.service
systemctl --user restart whisper-local.service
systemctl --user status whisper-local.service --no-pager
printf '\n服务已设置为用户登录后自动启动。查看日志：journalctl --user -u whisper-local.service -f\n'
printf '停止并取消开机自启：systemctl --user disable --now whisper-local.service\n'
printf '若要无用户登录也运行，需手动执行：sudo loginctl enable-linger "$USER"（可选）\n'

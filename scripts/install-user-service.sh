#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "$ROOT/scripts/common.sh"
PYTHON="$(resolve_python)"
command -v systemctl >/dev/null || { echo '需要 systemd user service' >&2; exit 1; }
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/whisper-local.service" <<EOF
[Unit]
Description=Whisper Local CPU (private transcription workstation)
After=default.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=/usr/bin/bash "$ROOT/scripts/run.sh"
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
Nice=10
CPUWeight=50
IOSchedulingClass=idle
MemoryMax=8G
TasksMax=128

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable whisper-local.service
systemctl --user restart whisper-local.service
systemctl --user --no-pager status whisper-local.service || true

# Whisper Local | 通用 Ubuntu 部署与配置指南

**目标**：在 Ubuntu 20.04、22.04、24.04（尤其 x86_64）部署一个本地音频工作台，无 OpenAI API Key；首次下载软件包和模型时需要网络，之后 CPU 本地推理。处理他人语音前取得知情同意。

## 0. 系统依赖

- CPU：现代 Intel/AMD x86_64 为主，建议至少 4 GB RAM；旧款 CPU 请选择 tiny 或 base。此项目不使用 CUDA。ARM Ubuntu 需自行核对对应 PyTorch/Whisper wheel 的支持。
- Python：3.10–3.13，推荐 3.11。20.04 默认 Python 3.8 不适用，使用 Miniforge/Conda 提供 3.11；22.04 默认 3.10，24.04 默认 3.12。
- FFmpeg/ffprobe：可从 Ubuntu APT 或 Conda 环境安装。首轮安装 PyTorch CPU 轮子需要较多空间和下载流量，模型首次运行也需要下载。

## 1. 首次部署

```bash
sudo apt update
sudo apt install -y git ffmpeg curl
# 在 Ubuntu 22.04/24.04，使用 venv 通常需要：
sudo apt install -y python3-venv
mkdir -p ~/apps && cd ~/apps
git clone https://github.com/1998x-stack/whisper-local-kit.git
cd whisper-local-kit
cp .env.example .env
nano .env
bash scripts/install.sh
bash scripts/check.sh
bash scripts/prefetch-models.sh tiny
bash scripts/run.sh
```

本机访问 `http://127.0.0.1:7865`，上传 10 秒中文录音测试。使用 `bash scripts/prefetch-models.sh base` 额外准备 base 模型。

**Conda 路线**： `INSTALL_BACKEND=conda bash scripts/install.sh`，名称默认 `whisper-cpu`；如已安装该环境，脚本优先复用。**venv 路线**：`INSTALL_BACKEND=venv bash scripts/install.sh`，使用仓库内 `.venv`，不改变系统 Python。`PYTHON_BIN=/usr/bin/python3.12` 指定 Python 位置。脚本不自动更新操作系统或修改 APT 软件源；清华镜像 403 时，可通过 Conda 官方 conda-forge 或修复自己原来的软件源。若下载失败，先看网络代理/镜像错误，不要反复在 base 环境装 pip 包。

## 2. `.env` 详细参数

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `WHISPER_PORT` | 7865 | 仅本机监听的 Web 服务端口 |
| `WHISPER_THREADS` | 2 | PyTorch CPU 推理线程（约 1–8） |
| `WHISPER_MAX_UPLOAD_MB` | 200 | 最大上传文件大小，强制上限 1024 MB |
| `WHISPER_MAX_DURATION_SECONDS` | 7200 | 最长音频时长（秒） |
| `WHISPER_MAX_OUTSTANDING` | 3 | 上传、排队、运行任务的总数量上限，1–20 |
| `WHISPER_RETENTION_DAYS` | 7 | 任务历史与转录会话的本地清理天数（1–365） |
| `WHISPER_DATA_DIR` | `$HOME/.local/share/whisper-local` | 任务元数据与输出文件 |
| `WHISPER_MODEL_DIR` | `$HOME/.cache/whisper` | Whisper 模型缓存 |
| `WHISPER_BATCH_ROOTS` | `$HOME` | Web 批处理允许访问的本地文件夹，可用冒号分隔多个目录 |
| `WHISPER_PYTHON` | 自动发现 | 绝对路径指向自定义 Python |
| `WHISPER_CONDA_ENV` | whisper-cpu | Conda 环境名称 |

`.env` 示例：

```bash
WHISPER_PORT=7865
WHISPER_THREADS=2
WHISPER_MAX_UPLOAD_MB=250
WHISPER_RETENTION_DAYS=15
WHISPER_BATCH_ROOTS=$HOME/Recordings:/mnt/media
WHISPER_DATA_DIR=$HOME/.local/share/whisper-local
```

要使用 `/mnt/media`，请先挂载设备，并确保当前用户能够读取 MP3 和写入 SRT。`.env` 是受信任的本地 Bash 配置，不能随意粘贴陌生配置文本；它不会提交到 Git。

## 3. 三种模式与字幕

- 上传模式：MP3、WAV、M4A、MP4 等 → TXT、SRT、VTT、JSON。
- 麦克风监听：浏览器本地音量活动检测 (SAD)，声音开始采集、静音后分段交给单 CPU 队列。**不是即时流式识别**，处理速度慢时文字会延迟；SRT 时间戳相对于每次启动监听时刻，不会压缩中间静音。必须由用户主动启动并授权麦克风，结束后停止采集。
- 批量：Web 中填写已授权的本机文件夹路径，默认跳过已存在 SRT；生成 `name.mp3` 对应的 `name.srt`。递归时维持子文件夹结构，最多一次 300 文件。

无需 Web 的批量 CLI（同样要在运行环境中执行）：

```bash
# venv 安装
.venv/bin/python scripts/batch_srt.py --input "$HOME/Recordings" --model tiny --language zh --dry-run
.venv/bin/python scripts/batch_srt.py --input "$HOME/Recordings" --model tiny --language zh
# Conda 安装
conda run -n whisper-cpu python scripts/batch_srt.py --input "$HOME/Recordings" --model base --language zh
```

## 4. 后台服务

```bash
# Ctrl+C 停止前台服务，然后：
bash scripts/install-user-service.sh
systemctl --user status whisper-local.service
journalctl --user -u whisper-local.service -n 80 --no-pager
systemctl --user restart whisper-local.service
systemctl --user disable --now whisper-local.service
```

服务依赖本项目 `scripts/run.sh`，不固定个人 Miniconda 安装路径。**这是用户服务**：Ubuntu 桌面登录后运行；非登录服务器环境请先了解 systemd linger 与系统服务的区别。应用自身是单进程、单执行队列，不要配置多个 uvicorn worker 以免重复消费或重复模型内存。

## 5. 远程安全

该系统无身份认证，固定监听 `127.0.0.1`，不要改成 `0.0.0.0` 并直接开放公网。要从自己的另一台电脑浏览可使用可信 SSH 隧道：

```bash
ssh -L 7865:127.0.0.1:7865 youruser@ubuntu-host
```

在运行 SSH 的电脑浏览 `http://127.0.0.1:7865`。如果开发正式多人版，需先增加鉴权、角色权限、CSRF/CORS 策略、请求频率与下载授权。

## 6. 升级、迁移、卸载

```bash
systemctl --user stop whisper-local.service
cd ~/apps/whisper-local-kit
git status --short
git pull --ff-only
bash scripts/install.sh
bash scripts/check.sh
systemctl --user start whisper-local.service
```

模型缓存在 `WHISPER_MODEL_DIR`，转录文件在 `WHISPER_DATA_DIR`，因此代码升级通常不需要重新下载模型；搬到新 Ubuntu 时迁移两个目录并保留私有权限。卸载先 `systemctl --user disable --now whisper-local.service`，再移除 `~/.config/systemd/user/whisper-local.service` 并 `systemctl --user daemon-reload`。只有在自己确认后才删除模型和私人录音文本。

## 7. 常见故障

| 表现 | 可能原因 | 排查 |
| --- | --- | --- |
| `python -m pip: No module named pip` | Conda 环境缺少 pip | `INSTALL_BACKEND=conda bash scripts/install.sh` |
| 403 Forbidden | 已配置镜像站拒绝下载 | 通过 conda-forge / 官方 pip 源检查网络；不要改动系统 Python |
| 音频解码失败 | FFmpeg 不存在或格式损坏 | `bash scripts/check.sh`、`ffprobe input.mp3` |
| 麦克风权限拒绝 | 非安全上下文 / 未授权 | 本地用 `127.0.0.1`、检查浏览器站点权限 |
| 批量目录不允许 | 未配置授权根目录 | 修改 `WHISPER_BATCH_ROOTS` 并重启服务 |
| SRT 很慢 | 旧 CPU 上 base/small 推理耗时 | 改用 tiny，减少同时任务，不要强开 CUDA |
| 服务启动失败 | 端口占用、安装路径变化 | `journalctl --user -u whisper-local.service -n 80` |

## 8. 已知局限

- Whisper 在非语音噪声上有可能生成错误文字；SAD 只是声能检测，后续可引入 WebRTC VAD / Silero VAD。
- 本地文件夹的 Web 模式只允许显式授权目录，仍不适合公开网络使用。
- 内存任务队列不是数据库消息队列；重启时正在进行的任务标记为中断，不能自动续跑。批量处理有同名 SRT 跳过机制，允许重新提交继续未完成工作。
- 初次下载模型需要网络；部署到完全离线主机时须事先在其他机器缓存模型和依赖。

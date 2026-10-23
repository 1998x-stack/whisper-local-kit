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
| `WHISPER_HOST` | `127.0.0.1` | 监听地址；局域网访问时可设为本机网卡 IP，不支持 `0.0.0.0` |
| `WHISPER_HOST_MIC_TOKEN` | 未设置 | 主机麦克风远程控制口令；用 `openssl rand -hex 24` 生成，勿分享 |
| `WHISPER_CAPTURE_DEVICE` | `default` | Ubuntu 主机 ALSA 录音设备名称，可用 `arecord -l` 查看 |
| `WHISPER_THREADS` | 2 | PyTorch CPU 推理线程（约 1–8） |
| `WHISPER_MAX_UPLOAD_MB` | 200 | 最大上传文件大小，强制上限 1024 MB |
| `WHISPER_MAX_DURATION_SECONDS` | 7200 | 最长音频时长（秒） |
| `WHISPER_MAX_OUTSTANDING` | 3 | 普通上传/批处理队列上限；实时片段另有 8 个名额 |
| `WHISPER_RETENTION_DAYS` | 7 | 任务历史与转录会话的本地清理天数（1–365） |
| `WHISPER_DATA_DIR` | `$HOME/.local/share/whisper-local` | 任务元数据与输出文件 |
| `WHISPER_MODEL_DIR` | `$HOME/.cache/whisper` | Whisper 模型缓存 |
| `WHISPER_BATCH_ROOTS` | `~/Music`、`~/Downloads`、`~/Documents` 中存在的目录 | Web 批量处理授权根目录；其他路径需要明确配置，冒号分隔 |
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

- 上传模式：MP3、WAV、M4A、MP4 等 → TXT、SRT、VTT、JSON。任务完成后默认显示 SRT 预览，下载沿用原文件名；中文转写默认转换为简体，旧任务展示和下载时也转换，原存档不改动。
- 麦克风监听：默认在 Ubuntu 主机运行 `arecord`，由网页按钮控制开始/停止，需输入 `WHISPER_HOST_MIC_TOKEN`；可切换为浏览器麦克风模式。两种模式均使用音量活动检测 (SAD)，声音结束后分段交给单 CPU 队列。**不是即时流式识别**；SRT 时间戳相对于每次监听起点，不压缩静音。主机模式关闭网页后仍继续采集，务必主动停止；浏览器模式关闭网页会停止采集。
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

该系统无身份认证，默认监听 `127.0.0.1`，不要设置为 `0.0.0.0` 或开放公网。若要从可信局域网访问，可在 `.env` 中设置 `WHISPER_HOST` 为服务器网卡 IP，并重启服务；能访问该 IP 和端口的设备都能读取转写记录并提交任务。主机麦克风口令只保护采集控制；普通 HTTP 传输不加密，敏感录音请使用可信 SSH 隧道或 HTTPS。

局域网的普通 `http://IP:7865` 页面可用于上传、批量处理和**主机麦克风模式**；浏览器麦克风模式需要 HTTPS 或 `localhost` 安全上下文。要在另一台电脑使用其浏览器麦克风，可通过可信 SSH 隧道把服务映射到那台电脑的 localhost：

```bash
ssh -L 7865:127.0.0.1:7865 youruser@ubuntu-host
```

如果设置了 `WHISPER_HOST`，将隧道命令中的目标 `127.0.0.1` 换成该网卡 IP。在运行 SSH 的电脑浏览 `http://127.0.0.1:7865`。如果开发正式多人版，需先增加鉴权、角色权限、CSRF/CORS 策略、请求频率与下载授权。

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

## 9. v3 性能、正确性与隐私改进

- **调度**：独立 live（最多 8 个排队/运行）与普通任务（默认最多 3 个）预算；优先级为实时监听 > 上传 > 批量。批量处理每个文件后让出 CPU 推理线程。已在解码中的一个文件不可中断。
- **解码**：统一 greedy（`beam_size=None`）与 `condition_on_previous_text=False`。离线转写使用 `(0, 0.2, 0.4)` 温度回退；实时片段固定 `0`，避免无意义额外解码。
- **置信度**：`WHISPER_FILTER_LOW_CONFIDENCE=1` 只在 `no_speech_prob >= 0.85` 且 `avg_logprob < -1.2` 同时满足时抑制片段；可设 `0` 关闭。保留得分到 JSON，仍需人工校对。
- **中文提示**：`WHISPER_ZH_INITIAL_PROMPT` 默认空；可按自己的音频选择简体中文术语提示，错误提示可能适得其反。
- **批处理无语音**：用 `.srt.no-speech` 标记代替空 SRT；成功与失败混合时为 `completed_with_errors`。
- **轻量响应**：任务列表隐藏每个 live 子任务，只返回上传与批量任务的前 1000 字文本；完整结果可从单任务 API 获取。
- **数据保留**：服务运行中每小时清理超过 `WHISPER_RETENTION_DAYS` 的已结束任务与旧会话数据。保留本地原始媒体目录不受影响。
- **安全**：Web 默认只放行 Music、Downloads、Documents 中存在的目录；禁用隐藏目录访问，并限制每次扫描 20,000 个条目。其他目录显式设置 `WHISPER_BATCH_ROOTS`。
- **性能核验**：记录上传转写的音频秒数、推理秒数及 RTF。模型引擎仍是 PyTorch CPU；在未测试目标 CPU AVX 兼容性前不自动替换为 CTranslate2。

CI 使用无需下载 Whisper 模型的 stub 测试，不是实际老 CPU 的速度与准确度测量。

## 10. 在真实 Ubuntu 上测量 RTF（不要用其他机器的推测值）

在完成安装并缓存 tiny/base 模型之后：

```bash
cd ~/apps/whisper-local-kit
# 使用实际录制、已获授权的 30–60 秒样本
.venv/bin/python scripts/bench.py --audio ~/Music/sample.wav --models tiny base --repeat 2 --output /tmp/whisper-bench.json
cat /tmp/whisper-bench.json
```

该脚本测量模型加载耗时、每轮推理时长、RTF 中位数和 CPU AVX/AVX2 特征。RTF < 1 表示该基准音频平均推理速度快于其录制时长；并不等同于浏览器监听零延迟。音频及转录内容不会写进基准 JSON。
没有经过目标机器真实基准前，不能保证更换 faster-whisper、whisper.cpp 或量化会加速；没有 AVX2 的旧处理器尤其需要先检查兼容性。

**systemd Python 解析**：安装用户服务时，会把检测到的 Python 绝对路径保存到服务单元环境变量；重建 venv/Conda 后应重新运行 `bash scripts/install-user-service.sh`。

**CI 覆盖**：Ubuntu 22.04/24.04 × Python 3.10/3.11/3.12 进行 stub 模型、JS 与 Shell 语法测试；不代表实测麦克风和老 CPU。

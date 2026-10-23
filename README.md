# Whisper Local v2.0 · 三合一离线语音工作台

**目标电脑**：Ubuntu 20.04 / 16 GB RAM / Intel i7-3687U CPU / Lenovo SSD / FFmpeg / Conda `whisper-cpu`。默认仅绑定 `127.0.0.1:7865`，不使用任何远程语音 API、无需 OpenAI API Key。**初次安装软件包及模型需要联网。** 上传、麦克风监听和批量任务共享一个 CPU 队列，建议不要在批量转写时同时开启监听。

## 新版三种模式

1. **上传音视频**：MP3/WAV/M4A/MP4 等 → TXT、SRT、VTT、JSON，继承 v1 单工作线程与任务历史。
2. **智能声音监听**：浏览器点击按钮后请求麦克风权限，前 1.4 秒校准环境声；适应性 RMS 能量门控 + 180 ms 触发 + 800 ms 静音结束 + 350 ms 预录缓冲；声音段分批发到本机的 Whisper CPU 队列；SRT 时间轴按本次监听起点计时，静音间隙不被压缩。停止时主动关闭麦克风。
3. **批量文件夹**：填写服务器本机文件夹，查找 `.mp3`（可选递归），依次转写，生成 **相同文件名**的 `.srt`。默认保存到原目录、已存在的 SRT 不覆盖；也可指定另一个输出目录，递归时保留子目录结构。每次最多 300 个 MP3。

> 声音检测 SAD 不等于人声检测 VAD：音乐、敲击或电风扇等突发噪声也可能触发；Whisper 可能对非人声产生错误文本。录音必须获得当事人同意，不宜无人看管记录他人的私人谈话。

## 1. 从 v1 直接升级（推荐）

建议先把新版 ZIP 解压到 `~/whisper-local-v2`。项目目录不要放在共享的 HDD 开放目录。

```bash
cd ~
unzip -o ~/Downloads/whisper-local-v2.zip
cd ~/whisper-local-v2

# 重要：先停止旧 v1 用户服务，避免端口冲突
systemctl --user stop whisper-local.service 2>/dev/null || true

# 自动检查 whisper-cpu 环境、pip、FFmpeg、PyTorch CPU、Whisper、FastAPI
bash scripts/install.sh

# 初次模型预下载（tiny 优先）
bash scripts/prefetch-models.sh tiny

# 前台启动进行第一次验证
bash scripts/run.sh
```

用同一台 Ubuntu 浏览器打开 **http://127.0.0.1:7865**。前台服务通过 `Ctrl+C` 退出。

### 已存在 v1 的用户服务如何切到 v2？

确保前台服务已经停止，再执行：

```bash
cd ~/whisper-local-v2
bash scripts/install-user-service.sh
systemctl --user status whisper-local.service --no-pager
```

新版 `install-user-service.sh` 会将 systemd 的 `WorkingDirectory` 更新为 v2，并**重启**旧服务；无需重新配置 GPU 或 UEFI。

查看后台日志：

```bash
journalctl --user -u whisper-local.service -n 80 --no-pager
```

停止后台服务：

```bash
systemctl --user disable --now whisper-local.service
```

## 2. 智能麦克风监听（模式 2）

在网页选择「智能声音监听」，选择 `tiny` 和中文，点击 **开启麦克风监听**，允许浏览器使用麦克风。音频仅在明显声音出现时组成短片段并交给本机服务；静音时仅执行轻量声音能量计算，不向 Whisper 提交转写任务。使用完点击「停止监听并生成字幕」，网页提供本次会话完整 SRT/TXT/VTT/JSON 下载。可以在时间轴上看到发生讲话的对应时刻。

核心参数：

| 参数 | 默认 | 作用 |
| --- | ---: | --- |
| 噪声校准 | 1.4s | 估计当前环境声强度 |
| 动态触发差值 | +12 dB | 高于估计背景的能量触发，界面可调 6–20 dB |
| 激活最短持续时间 | 180ms | 排除短促噪声的一部分 |
| 静音结束 | 800ms | 结束一个片段，提交转写 |
| 前置录音 | 350ms | 尽量避免丢掉一句话开头 |
| 最大片段 | 22s | 分段防止长时间 CPU 堆积；可能出现少量重叠字词 |

监听以**浏览器音频流累计采样**为时间轴：如果第一句话发生在 00:35、第二句话在 01:02，SRT 就保留中间空白，不会把第二句硬接在上一句后。由于电脑算力较弱，实际文字可能在说完后较长时间才出现（CPU 处理队列，不是流式 token 即时转写）。

**注意**：关闭网页/休眠会终止麦克风捕获；有时突然关页会丢失最后未提交的半句。浏览器必须处在 HTTPS 或 `localhost/127.0.0.1` 的安全上下文里才能申请麦克风权限。保持网页活动以避免后台节流。如累积排队超过 12 段，界面会提示丢段；重要录音应另行保存原始录音，不可把本系统视为无损录音机。

## 3. 文件夹 MP3 批量生成同名 SRT（模式 3）

如果电脑目录是：

```text
/mnt/sda4/media/课程/
├── A.mp3
├── B.mp3
└── C.mp3
```

在「批量 MP3 → SRT」中输入 `/mnt/sda4/media/课程`，输出路径留空，默认自动生成：

```text
/mnt/sda4/media/课程/
├── A.mp3
├── A.srt
├── B.mp3
├── B.srt
├── C.mp3
└── C.srt
```

输入目录、输出目录只能位于后端允许的本机目录内（默认 `~/` 和 `/mnt/sda4`）；需要其他目录，可以在服务启动前设置 `WHISPER_BATCH_ROOTS=/你的目录:/另一个目录`。允许创建指定输出目录，但不会删除输入 MP3。默认跳过已有 SRT，不会覆盖。批量任务重启后不会自动续跑，已写入 SRT 会保留，重复提交时“跳过已有 SRT”能从未处理部分接着进行。

### 终端独立批处理

本项目同时支持命令行方式，不需要开启 Web UI：

```bash
cd ~/whisper-local-v2
conda activate whisper-cpu
python scripts/batch_srt.py --input '/mnt/sda4/media/课程' --model tiny --language zh

# 递归扫描子文件夹，输出到另一个目录（维持相对目录结构）
python scripts/batch_srt.py --input '/mnt/sda4/media/课程' \
  --output '/mnt/sda4/media/字幕' --recursive --model base --language zh
```

默认存在同名 SRT 时跳过。先追加 `--dry-run` 可预览将处理的文件列表。命令行批处理为**独立进程**，不要与 Web 后台同时跑大量任务，避免抢占同一个老 CPU。

## 4. HTTP API（全本机，无鉴权）

```bash
curl http://127.0.0.1:7865/api/health
```

提交批量任务：

```bash
curl -X POST http://127.0.0.1:7865/api/batch \
  -H 'X-Local-Whisper-Client: 1' -H 'Content-Type: application/json' \
  -d '{"input_directory":"/mnt/sda4/media/课程","model":"tiny","language":"zh","skip_existing":true}'
```

监听 API：创建会话 `POST /api/live/sessions`，按 `offset_seconds` 上传 PCM WAV 分段到 `POST /api/live/{id}/chunks`，查询 `GET /api/live/{id}`，下载 `GET /api/live/{id}/download/srt`。网页会自动完成这些步骤。`X-Local-Whisper-Client: 1` 只是防跨站表单误提交的标记，**不是安全认证**。

## 5. 安全限制与常见故障

- **禁止公开端口**：服务无登录认证，绑定 `127.0.0.1`。不要设置 `--host 0.0.0.0`，不要在路由器转发 7865。远程访问通过可信 SSH 隧道实现。
- **目录权限**：文件夹模式只允许访问配置的本机目录，不能通过浏览器上传任意服务器文件夹路径。Web 使用者必须在本机，或通过可信 SSH 隧道。
- **不存储原始监听片段**：后台转写结束后删除收到的 WAV。转写后的文本留在本机用户目录 `~/.local/share/whisper-local`；请保护文件权限。
- **pip 缺失**：`conda install -n whisper-cpu --override-channels -c https://conda.anaconda.org/conda-forge pip -y`。
- **FFmpeg 缺失**：同样通过 conda-forge 为 `whisper-cpu` 环境安装 FFmpeg。
- **旧 v1 网页仍出现**：确认用户服务 WorkingDirectory 已指向新项目，再刷新页面（Ctrl+F5）。
- **浏览器不给麦克风权限**：确认使用 `http://127.0.0.1:7865`，不要直接用 `http://192.168.x.x:7865`；并检查浏览器站点麦克风权限。
- **出现识别不准或幻觉**：使用 `base`，尽量靠近麦克风，手动调整触发灵敏度，必要时人工校对字幕。

## 6. 研究与资料

- OpenAI Whisper：<https://github.com/openai/whisper>
- MDN `getUserMedia`：<https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia>
- MDN AudioWorklet：<https://developer.mozilla.org/en-US/docs/Web/API/AudioWorklet>
- Google WebRTC VAD：<https://github.com/wiseman/py-webrtcvad>
- Silero VAD：<https://github.com/snakers4/silero-vad>

**验证范围**：代码结构、字幕时间偏移、批量目录扫描与非破坏性输出、HTTP API 使用模拟 Whisper 引擎检查。没有在你的 Ubuntu 上直接录音或跑完整模型推理；需要实际使用 10–30 秒样本完成端到端验收。

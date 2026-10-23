# Changelog

## 2.1.0 — portable Ubuntu deployment

- 新增 `.env.example` 和 `docs/DEPLOY_UBUNTU.md`，提供通用安装、配置、服务、升级与迁移说明。
- 支持 Python `.venv` 和已有 Conda 的自动检测，也可通过 `WHISPER_PYTHON` 自定义解释器。
- 端口、CPU 线程、任务队列长度、保留期限、上传限制、模型路径都可配置。
- Web 批量处理的默认授权根目录缩小到当前用户主目录；其他数据盘通过 `WHISPER_BATCH_ROOTS` 显式授权。
- 启动时清理超过保留天数的旧监听会话；保留上传任务中断恢复状态标记。
- 用户级 systemd 统一通过 `scripts/run.sh` 启动，避免写死 `~/miniconda3`。
- 新增跨机配置与目录边界测试，保留上传 / 声音门控 / 批处理原功能。

### 仍待解决

- 无账号登录和云端任务队列，仍不允许在公网直接暴露 HTTP API。
- 当前仍使用能量 SAD 而非人声 VAD，不能保证背景噪声不误触发。
- 任务队列使用单进程内存，服务重启不能断点恢复正在推理的片段。
- `RETENTION_DAYS` 清理在启动时触发；长时间不重启的服务可在后续版本加入独立清理调度。

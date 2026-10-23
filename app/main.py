"""CPU-first, loopback-only, keyless personal Whisper transcription server.

Run: python -m uvicorn app.main:app --host 127.0.0.1 --port 7865 --workers 1
Do not expose this unauthenticated application to an untrusted network.
"""
import json
import math
import os
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
import logging
from fastapi import Request
from .scheduler import PriorityExecutor
from .processing import decode_file, simple_segments, transcript_text
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from .subtitles import to_srt, to_vtt

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = Path(os.getenv("WHISPER_DATA_DIR", str(Path.home() / ".local/share/whisper-local"))).expanduser()
MODEL_DIR = Path(os.getenv("WHISPER_MODEL_DIR", str(Path.home() / ".cache/whisper"))).expanduser()
MAX_BYTES = min(1024, max(1, int(os.getenv("WHISPER_MAX_UPLOAD_MB", "200")))) * 1024 * 1024
MAX_DURATION = min(86400, max(30, int(os.getenv("WHISPER_MAX_DURATION_SECONDS", "7200"))))
THREADS = max(1, min(8, int(os.getenv("WHISPER_THREADS", "2"))))
MAX_OUTSTANDING = max(1, min(20, int(os.getenv("WHISPER_MAX_OUTSTANDING", "3"))))
RETENTION_DAYS = max(1, min(365, int(os.getenv("WHISPER_RETENTION_DAYS", "7"))))
ALLOWED_EXT = {".mp3", ".wav", ".m4a", ".mp4", ".mov", ".mkv", ".flac", ".ogg", ".opus", ".webm", ".aac", ".wma"}
MODEL_CHOICES = {"tiny", "base", "small"}
LANGUAGE_CHOICES = {"auto": None, "zh": "zh", "en": "en", "ja": "ja", "ko": "ko", "fr": "fr", "de": "de", "es": "es"}
TASK_CHOICES = {"transcribe", "translate"}

logger = logging.getLogger("whisper_local")


def sweep_expired() -> None:
    cutoff = time.time() - RETENTION_DAYS * 86400
    with lock:
        expired = [jid for jid, job in jobs.items()
                   if job["status"] not in {"uploading", "queued", "running"}
                   and metadata_path(jid).is_file()
                   and metadata_path(jid).stat().st_mtime < cutoff]
        for jid in expired:
            jobs.pop(jid, None)
    for jid in expired:
        shutil.rmtree(metadata_path(jid).parent, ignore_errors=True)
    from .extensions import prune_sessions
    prune_sessions(DATA_ROOT, cutoff)


def _sweep_loop(stop: threading.Event) -> None:
    while not stop.wait(3600):
        try:
            sweep_expired()
        except Exception:
            logger.exception("Periodic transcript cleanup failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    startup()
    stop = threading.Event()
    cleaner = threading.Thread(target=_sweep_loop, args=(stop,), daemon=True)
    cleaner.start()
    try:
        yield
    finally:
        stop.set()
        cleaner.join(timeout=2)


app = FastAPI(title="Whisper Local", version="3.0.0", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])
executor = PriorityExecutor()
lock = threading.Lock()
jobs: dict[str, dict[str, Any]] = {}
model_cache: dict[str, Any] = {"name": None, "model": None}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def metadata_path(job_id: str) -> Path:
    return DATA_ROOT / "jobs" / job_id / "status.json"


def save(job: dict[str, Any]) -> None:
    path = metadata_path(job["id"])
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def update(job_id: str, **changes: Any) -> None:
    with lock:
        job = jobs[job_id]
        job.update(changes)
        job["updated_at"] = utcnow()
        save(job)


def load_saved() -> None:
    job_root = DATA_ROOT / "jobs"
    job_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    expire_before = time.time() - RETENTION_DAYS * 86400
    for directory in job_root.iterdir():
        if not directory.is_dir():
            continue
        meta = directory / "status.json"
        if not meta.exists():
            continue
        if meta.stat().st_mtime < expire_before:
            shutil.rmtree(directory)
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            if data.get("status") in {"uploading", "queued", "running"}:
                data["status"] = "interrupted"
                data["error"] = "服务重启导致任务中断，请重新提交。"
                data["updated_at"] = utcnow()
                save(data)
            jobs[data["id"]] = data
        except (OSError, ValueError, KeyError):
            continue


def startup() -> None:
    os.umask(0o077)
    DATA_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    MODEL_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    load_saved()
    # Bound disk retention for live-session transcripts as well as uploaded jobs.
    live_root = DATA_ROOT / "live"
    if live_root.is_dir():
        cutoff = time.time() - RETENTION_DAYS * 86400
        for directory in live_root.iterdir():
            if directory.is_dir() and not directory.is_symlink():
                try:
                    uuid.UUID(directory.name)
                    if directory.stat().st_mtime < cutoff:
                        shutil.rmtree(directory)
                except (ValueError, OSError):
                    continue


@app.middleware("http")
async def privacy_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def index() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "web" / "index.html", media_type="text/html; charset=utf-8")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "engine": "openai-whisper / PyTorch CPU",
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "ffprobe": shutil.which("ffprobe") is not None,
        "threads": THREADS,
        "models": sorted(MODEL_CHOICES),
        "max_upload_mb": MAX_BYTES // (1024 * 1024),
        "max_duration_seconds": MAX_DURATION,
        "no_api_key": True,
        "scheduler": "live > upload > batch-file",
        "bind_recommendation": "127.0.0.1 ONLY",
    }


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    with lock:
        recent = sorted((j for j in jobs.values() if j.get("kind") != "live"),
                        key=lambda j: j["created_at"], reverse=True)[:30]
        return [{**{k: v for k, v in j.items() if k != "text"},
                 "text_preview": str(j.get("text", ""))[:1000],
                 "errors": j.get("errors", [])[:3]} for j in recent]


def get_job_or_404(job_id: str) -> dict[str, Any]:
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="任务不存在")
    with lock:
        j = jobs.get(job_id)
        if j is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return dict(j)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    return get_job_or_404(job_id)


@app.delete("/api/jobs/{job_id}")
def delete_job(
    job_id: str,
    x_local_whisper_client: str | None = Header(default=None, alias="X-Local-Whisper-Client"),
) -> dict[str, bool]:
    if x_local_whisper_client != "1":
        raise HTTPException(403, "缺少本机请求标记")
    get_job_or_404(job_id)
    with lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "任务不存在")
        if job["status"] in {"uploading", "queued", "running"}:
            raise HTTPException(409, "任务尚未结束，不能删除")
        jobs.pop(job_id)
    shutil.rmtree(metadata_path(job_id).parent, ignore_errors=True)
    return {"deleted": True}


@app.get("/api/jobs/{job_id}/download/{fmt}")
def download(job_id: str, fmt: str) -> FileResponse:
    job = get_job_or_404(job_id)
    if job["status"] != "complete" or fmt not in {"txt", "srt", "vtt", "json"}:
        raise HTTPException(status_code=404, detail="文件不可用")
    path = metadata_path(job_id).parent / f"result.{fmt}"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path, filename=f"whisper-{job_id[:8]}.{fmt}")


def probe_audio(path: Path) -> float:
    command = ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    if proc.returncode != 0:
        raise ValueError("FFmpeg 无法读取此文件，请检查格式或文件完整性")
    info = json.loads(proc.stdout)
    if not any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
        raise ValueError("此文件没有可识别的音轨")
    duration = float(info.get("format", {}).get("duration", "nan"))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("无法读取有效音频时长")
    if duration > MAX_DURATION:
        raise ValueError(f"文件过长；当前限制 {MAX_DURATION // 60} 分钟")
    return duration


def load_model(name: str) -> Any:
    import gc
    import torch
    import whisper
    torch.set_num_threads(THREADS)
    if model_cache["name"] != name:
        model_cache["model"] = None
        model_cache["name"] = None
        gc.collect()
        model_cache["model"] = whisper.load_model(name, device="cpu", download_root=str(MODEL_DIR))
        model_cache["name"] = name
    return model_cache["model"]


def process_job(job_id: str, input_path: Path, model_name: str, language: str, task: str) -> None:
    folder = metadata_path(job_id).parent
    try:
        update(job_id, status="running", stage="读取音频格式")
        duration = probe_audio(input_path)
        update(job_id, duration_seconds=round(duration, 2), stage=f"加载 {model_name} 模型（首次使用可能联网下载）")
        model = load_model(model_name)
        update(job_id, stage="正在 CPU 转写：长音频可能耗时较久")
        started = time.monotonic()
        result = decode_file(model, input_path, LANGUAGE_CHOICES[language], task=task)
        inference_seconds = time.monotonic() - started
        segments = simple_segments(result)
        text = transcript_text(result, segments)
        (folder / "result.txt").write_text(text + ("\n" if text else ""), encoding="utf-8")
        (folder / "result.srt").write_text(to_srt(segments), encoding="utf-8")
        (folder / "result.vtt").write_text(to_vtt(segments), encoding="utf-8")
        (folder / "result.json").write_text(
            json.dumps({"text": text, "language": result.get("language"), "segments": segments}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        rtf = round(inference_seconds / duration, 3)
        logger.info("transcribed job=%s model=%s audio_s=%.2f inference_s=%.2f rtf=%.3f",
                    job_id, model_name, duration, inference_seconds, rtf)
        update(job_id, status="complete", stage="完成", text=text,
               inference_seconds=round(inference_seconds, 2), real_time_factor=rtf,
               language_detected=result.get("language"), segments_count=len(segments))
    except Exception as exc:
        update(job_id, status="failed", stage="失败", error=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            input_path.unlink(missing_ok=True)
        except OSError:
            pass


@app.post("/api/transcribe", status_code=202)
async def transcribe(
    request: Request,
    file: UploadFile = File(...),
    model: str = Form("tiny"),
    language: str = Form("zh"),
    task: str = Form("transcribe"),
    x_local_whisper_client: str | None = Header(default=None, alias="X-Local-Whisper-Client"),
) -> dict[str, str]:
    # Non-secret custom header: prevents a simple cross-site HTML form from submitting jobs.
    # This is NOT authentication. Keep the server bound to 127.0.0.1.
    if x_local_whisper_client != "1":
        raise HTTPException(status_code=403, detail="需要 X-Local-Whisper-Client: 1 请求头")
    if model not in MODEL_CHOICES or language not in LANGUAGE_CHOICES or task not in TASK_CHOICES:
        raise HTTPException(status_code=400, detail="模型、语言或任务参数不正确")
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdecimal() and int(content_length) > MAX_BYTES + 65536:
        raise HTTPException(413, f"请求体超过限制（文件最多 {MAX_BYTES // 1048576} MB）")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="不支持的文件扩展名")
    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        raise HTTPException(status_code=503, detail="FFmpeg / ffprobe 未安装或不在 PATH")

    with lock:
        outstanding = sum(j["status"] in ("uploading", "queued", "running")
                          and j.get("kind") != "live" for j in jobs.values())
        if outstanding >= MAX_OUTSTANDING:
            raise HTTPException(status_code=429, detail="任务队列已满，请完成现有任务后再提交")
        job_id = str(uuid.uuid4())
        now = utcnow()
        data: dict[str, Any] = {
            "id": job_id, "name": Path(file.filename or "audio").name[:120], "model": model,
            "requested_language": language, "task": task, "status": "uploading",
            "stage": "上传中", "created_at": now, "updated_at": now,
        }
        jobs[job_id] = data
        save(data)

    folder = metadata_path(job_id).parent
    incoming = folder / ("source" + suffix)
    total = 0
    try:
        with incoming.open("wb") as output:
            while True:
                data_bytes = await file.read(1024 * 1024)
                if not data_bytes:
                    break
                total += len(data_bytes)
                if total > MAX_BYTES:
                    raise HTTPException(status_code=413, detail=f"文件不能超过 {MAX_BYTES // (1024 * 1024)} MB")
                output.write(data_bytes)
        if total == 0:
            raise HTTPException(status_code=400, detail="上传文件为空")
        update(job_id, status="queued", stage="等待 CPU 队列", size_bytes=total)
        executor.submit(process_job, job_id, incoming, model, language, task, priority=10)
        return {"id": job_id, "status": "queued", "poll_url": f"/api/jobs/{job_id}"}
    except Exception as exc:
        incoming.unlink(missing_ok=True)
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        update(job_id, status="failed", stage="上传失败", error=str(detail)[:300])
        raise
    finally:
        await file.close()

# v3: extensions share the priority-aware single CPU inference lane.
from .extensions import register as register_extensions  # noqa: E402
register_extensions(app, globals())

@app.get("/assets/{filename}")
def get_asset(filename: str) -> FileResponse:
    names = {"sound-gate.js", "recorder-worklet.js", "ui.js", "styles.css"}
    if filename not in names:
        raise HTTPException(404, "文件不存在")
    asset = PROJECT_ROOT / "web" / filename
    content_type = "text/javascript" if filename.endswith(".js") else "text/css"
    return FileResponse(asset, media_type=content_type)

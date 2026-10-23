"""Local-only real-time clip and approved-folder batch extension.

No remote models, no background microphone capture. Server accepts audio only after
explicit browser permission and a user-triggered start event.
"""
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import Body, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .subtitles import to_srt, to_vtt

SRT_LANGS = {"zh", "auto", "en", "ja", "ko", "fr", "de", "es"}
MEDIA_SUFFIXES = {".mp3"}
BATCH_LIMIT = 300
MAX_CLIP_BYTES = 24 * 1024 * 1024
SESSION_LOCK = threading.RLock()
sessions: dict[str, dict[str, Any]] = {}


def allowed_roots() -> list[Path]:
    configured = os.getenv("WHISPER_BATCH_ROOTS", "")
    values = configured.split(":") if configured else [str(Path.home()), "/mnt/sda4"]
    return [p for v in values if v.strip() if (p := Path(v).expanduser().resolve()).is_dir()]


def safe_directory(value: str, *, create: bool = False) -> Path:
    if not value.strip():
        raise HTTPException(400, "文件夹路径不能为空")
    candidate = Path(value).expanduser().resolve()
    if not any(candidate == root or candidate.is_relative_to(root) for root in allowed_roots()):
        raise HTTPException(403, "文件夹不在允许的本地目录中（默认：用户主目录或 /mnt/sda4）")
    if create:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(400, f"无法创建输出目录: {exc}")
    if not candidate.is_dir():
        raise HTTPException(400, "文件夹不存在或无法访问")
    return candidate


def list_mp3(directory: Path, recursive: bool) -> list[Path]:
    iterator = directory.rglob("*") if recursive else directory.iterdir()
    files = []
    for p in iterator:
        if p.suffix.lower() != ".mp3" or not p.is_file():
            continue
        resolved = p.resolve()
        if resolved.is_relative_to(directory):
            files.append(p)
        if len(files) > BATCH_LIMIT:
            raise HTTPException(400, f"一次最多 {BATCH_LIMIT} 个 MP3，建议分批提交")
    return sorted(files, key=lambda p: str(p).casefold())


def safe_output(path: Path, output_root: Path) -> bool:
    """Avoid writing through an existing symlink or symlinked directory."""
    parent = path.parent.resolve()
    return parent.is_relative_to(output_root) and not path.is_symlink()


def simple_segments(result: dict[str, Any], offset: float = 0.0, duration: float | None = None) -> list[dict[str, Any]]:
    out = []
    for item in result.get("segments", []):
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        start = max(0.0, float(item.get("start", 0)))
        end = max(start, float(item.get("end", start)))
        if duration is not None:
            start = min(start, duration)
            end = min(end, duration)
        if end <= start:
            continue
        out.append({"start": round(start + offset, 3), "end": round(end + offset, 3), "text": text})
    return out


def atomic_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class BatchRequest(BaseModel):
    input_directory: str = Field(min_length=1, max_length=1024)
    output_directory: str | None = Field(default=None, max_length=1024)
    recursive: bool = False
    skip_existing: bool = True
    model: str = "tiny"
    language: str = "zh"


def register(app: Any, ctx: dict[str, Any]) -> None:
    """Extend original v1 app while reusing its single shared Whisper CPU worker."""
    def job_create(kind: str, name: str, model: str, language: str, **data: Any) -> str:
        with ctx["lock"]:
            outstanding = sum(j["status"] in {"uploading", "queued", "running"} for j in ctx["jobs"].values())
            if outstanding >= ctx["MAX_OUTSTANDING"]:
                raise HTTPException(429, "CPU 任务队列已满，稍后重试")
            jid = str(uuid.uuid4())
            stamp = ctx["utcnow"]()
            j = {"id": jid, "kind": kind, "name": name[:120], "model": model,
                 "requested_language": language, "task": "transcribe", "status": "queued",
                 "stage": "等待 CPU 队列", "created_at": stamp, "updated_at": stamp, **data}
            ctx["jobs"][jid] = j
            ctx["save"](j)
            return jid

    def check_header(header: str | None) -> None:
        if header != "1":
            raise HTTPException(403, "缺少本机请求标记；这不是 API Key")

    def session_location(sid: str) -> Path:
        try:
            uuid.UUID(sid)
        except ValueError:
            raise HTTPException(404, "监听会话不存在")
        return ctx["DATA_ROOT"] / "live" / sid

    def persist_session(session: dict[str, Any]) -> None:
        directory = session_location(session["id"])
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_text(directory / "session.json", json.dumps(session, ensure_ascii=False, indent=2))

    def get_session(sid: str) -> dict[str, Any]:
        session_location(sid)
        with SESSION_LOCK:
            if sid not in sessions:
                path = session_location(sid) / "session.json"
                if path.is_file():
                    try:
                        sessions[sid] = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        pass
            if sid not in sessions:
                raise HTTPException(404, "监听会话不存在")
            return dict(sessions[sid])

    @app.post("/api/live/sessions", status_code=201)
    def create_live(
        model: str = Body("tiny"), language: str = Body("zh"),
        x_local_whisper_client: str | None = Header(None, alias="X-Local-Whisper-Client"),
    ) -> dict[str, str]:
        check_header(x_local_whisper_client)
        if model not in ctx["MODEL_CHOICES"] or language not in SRT_LANGS:
            raise HTTPException(400, "模型或语言不正确")
        sid = str(uuid.uuid4())
        s = {"id": sid, "created_at": ctx["utcnow"](), "status": "monitoring",
             "model": model, "language": language, "segments": [], "submitted": 0,
             "completed": 0, "failed": 0, "last_error": None}
        with SESSION_LOCK:
            sessions[sid] = s
            persist_session(s)
        return {"id": sid}

    @app.get("/api/live/{sid}")
    def live_status(sid: str) -> dict[str, Any]:
        return get_session(sid)

    @app.post("/api/live/{sid}/stop")
    def stop_live(sid: str, x_local_whisper_client: str | None = Header(None, alias="X-Local-Whisper-Client")) -> dict[str, str]:
        check_header(x_local_whisper_client)
        get_session(sid)
        with SESSION_LOCK:
            sessions[sid]["status"] = "stopped"
            persist_session(sessions[sid])
        return {"status": "stopped"}

    @app.get("/api/live/{sid}/download/{fmt}")
    def download_live(sid: str, fmt: str) -> FileResponse:
        if fmt not in {"txt", "srt", "vtt", "json"}:
            raise HTTPException(404, "不支持的格式")
        s = get_session(sid)
        segments = sorted(s["segments"], key=lambda v: (v["start"], v["end"]))
        folder = session_location(sid)
        filename = folder / f"transcript.{fmt}"
        if fmt == "srt":
            content = to_srt(segments)
        elif fmt == "vtt":
            content = to_vtt(segments)
        elif fmt == "json":
            content = json.dumps({"session_id": sid, "segments": segments}, ensure_ascii=False, indent=2)
        else:
            content = "\n".join(x["text"] for x in segments) + ("\n" if segments else "")
        atomic_text(filename, content)
        return FileResponse(filename, filename=f"live-{sid[:8]}.{fmt}")

    def run_live(jid: str, file: Path, model: str, language: str, sid: str, offset: float, duration: float) -> None:
        try:
            ctx["update"](jid, status="running", stage="监听片段转写中")
            ctx["probe_audio"](file)
            engine = ctx["load_model"](model)
            result = engine.transcribe(str(file), language=ctx["LANGUAGE_CHOICES"][language], task="transcribe",
                                       fp16=False, verbose=None, temperature=0, beam_size=1, best_of=1,
                                       condition_on_previous_text=False)
            segments = simple_segments(result, offset=offset, duration=duration)
            with SESSION_LOCK:
                s = sessions[sid]
                s["segments"].extend(segments)
                s["segments"].sort(key=lambda v: (v["start"], v["end"]))
                s["completed"] += 1
                persist_session(s)
            ctx["update"](jid, status="complete", stage="完成", segments_count=len(segments))
        except Exception as exc:
            with SESSION_LOCK:
                s = sessions[sid]
                s["failed"] += 1
                s["last_error"] = str(exc)[:240]
                persist_session(s)
            ctx["update"](jid, status="failed", stage="失败", error=f"{type(exc).__name__}: {exc}")
        finally:
            file.unlink(missing_ok=True)

    @app.post("/api/live/{sid}/chunks", status_code=202)
    async def live_chunk(
        sid: str, file: UploadFile = File(...),
        offset_seconds: float = Form(...), duration_seconds: float = Form(...),
        x_local_whisper_client: str | None = Header(None, alias="X-Local-Whisper-Client"),
    ) -> dict[str, str]:
        check_header(x_local_whisper_client)
        s = get_session(sid)
        if s["status"] != "monitoring":
            raise HTTPException(409, "监听已经停止")
        if not 0 <= offset_seconds <= 86400 or not 0.25 <= duration_seconds <= 30:
            raise HTTPException(400, "录音时间偏移或片段长度不正确")
        if Path(file.filename or "").suffix.lower() != ".wav":
            raise HTTPException(400, "实时监听仅接受浏览器发送的 WAV PCM 音频")
        jid = job_create("live", f"监听 +{offset_seconds:.1f}s", s["model"], s["language"], session_id=sid)
        path = ctx["metadata_path"](jid).parent / "clip.wav"
        size = 0
        try:
            with path.open("wb") as f:
                while True:
                    chunk = await file.read(128 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_CLIP_BYTES:
                        raise HTTPException(413, "实时音频片段过大")
                    f.write(chunk)
            if not size:
                raise HTTPException(400, "收到空音频片段")
            with SESSION_LOCK:
                sessions[sid]["submitted"] += 1
                persist_session(sessions[sid])
            ctx["executor"].submit(run_live, jid, path, s["model"], s["language"], sid, offset_seconds, duration_seconds)
            return {"id": jid, "status": "queued"}
        except Exception:
            path.unlink(missing_ok=True)
            ctx["update"](jid, status="failed", stage="上传失败")
            raise
        finally:
            await file.close()

    def run_batch(jid: str, files: list[Path], inp: Path, out: Path, model: str, lang: str, skip_existing: bool) -> None:
        ok = fail = skipped = 0
        detail = []
        try:
            ctx["update"](jid, status="running", stage="加载 Whisper 模型", total_files=len(files))
            engine = ctx["load_model"](model)
            for idx, source in enumerate(files, 1):
                rel = source.relative_to(inp).with_suffix(".srt")
                destination = out / rel
                ctx["update"](jid, stage=f"[{idx}/{len(files)}] {source.name}", completed_files=ok,
                              failed_files=fail, skipped_files=skipped)
                try:
                    # Do not allow input symlinks to escape their approved base path.
                    if not source.resolve().is_relative_to(inp):
                        raise ValueError("文件链接指向扫描目录之外")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if not safe_output(destination, out):
                        raise ValueError("输出路径包含不安全的符号链接")
                    if skip_existing and destination.exists():
                        skipped += 1
                        continue
                    if source.stat().st_size > ctx["MAX_BYTES"]:
                        raise ValueError("文件超过单文件大小限制")
                    ctx["probe_audio"](source)
                    result = engine.transcribe(str(source), language=ctx["LANGUAGE_CHOICES"][lang],
                                               task="transcribe", fp16=False, verbose=None,
                                               temperature=0, beam_size=1, best_of=1)
                    segments = simple_segments(result)
                    atomic_text(destination, to_srt(segments))
                    ok += 1
                except Exception as exc:
                    fail += 1
                    detail.append({"file": str(rel), "error": str(exc)[:200]})
            ctx["update"](jid, status="complete" if not fail or ok or skipped else "failed", stage="批量处理结束",
                          completed_files=ok, failed_files=fail, skipped_files=skipped,
                          errors=detail[:100])
        except Exception as exc:
            ctx["update"](jid, status="failed", stage="处理失败", error=f"{type(exc).__name__}: {exc}")

    @app.post("/api/batch", status_code=202)
    def batch(request: BatchRequest, x_local_whisper_client: str | None = Header(None, alias="X-Local-Whisper-Client")) -> dict[str, Any]:
        check_header(x_local_whisper_client)
        if request.model not in ctx["MODEL_CHOICES"] or request.language not in SRT_LANGS:
            raise HTTPException(400, "模型或语言不支持")
        inp = safe_directory(request.input_directory)
        out = safe_directory(request.output_directory or request.input_directory, create=True)
        files = list_mp3(inp, request.recursive)
        if not files:
            raise HTTPException(400, "当前目录没有找到 MP3 文件")
        jid = job_create("batch", f"批量 MP3 · {inp.name}", request.model, request.language,
                         total_files=len(files), completed_files=0, failed_files=0, skipped_files=0,
                         output_directory=str(out))
        ctx["executor"].submit(run_batch, jid, files, inp, out, request.model, request.language, request.skip_existing)
        return {"id": jid, "total_files": len(files), "output_directory": str(out)}

    @app.post("/api/batch/preview")
    def preview_batch(request: BatchRequest, x_local_whisper_client: str | None = Header(None, alias="X-Local-Whisper-Client")) -> dict[str, Any]:
        check_header(x_local_whisper_client)
        inp = safe_directory(request.input_directory)
        files = list_mp3(inp, request.recursive)
        return {"count": len(files), "files": [str(f.relative_to(inp)) for f in files[:40]]}

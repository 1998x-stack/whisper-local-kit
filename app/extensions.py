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
from fastapi.responses import Response
from .processing import decode_file, simple_segments, atomic_text
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
    values = configured.split(":") if configured else [str(Path.home() / "Music"), str(Path.home() / "Downloads"), str(Path.home() / "Documents")]
    return [p for v in values if v.strip() if (p := Path(v).expanduser().resolve()).is_dir()]


def safe_directory(value: str, *, create: bool = False) -> Path:
    if not value.strip():
        raise HTTPException(400, "文件夹路径不能为空")
    candidate = Path(value).expanduser().resolve()
    if not any(candidate == root or candidate.is_relative_to(root) for root in allowed_roots()):
        raise HTTPException(403, "文件夹不在允许的本地目录中（请配置 WHISPER_BATCH_ROOTS，默认仅 Music/Downloads/Documents）")
    if any(part.startswith(".") for part in candidate.parts[1:]):
        raise HTTPException(403, "为安全起见，批处理路径不允许隐藏目录")
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
    scanned = 0
    for p in iterator:
        scanned += 1
        if scanned > 20000:
            raise HTTPException(400, "扫描超过 20000 个目录条目，请缩小范围")
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


class GapRequest(BaseModel):
    offset_seconds: float = Field(ge=0, le=86400)
    duration_seconds: float = Field(ge=0.05, le=30)


class BatchRequest(BaseModel):
    input_directory: str = Field(min_length=1, max_length=1024)
    output_directory: str | None = Field(default=None, max_length=1024)
    recursive: bool = False
    skip_existing: bool = True
    model: str = "tiny"
    language: str = "zh"


def prune_sessions(data_root: Path, cutoff: float) -> None:
    directory = data_root / "live"
    if not directory.is_dir():
        return
    with SESSION_LOCK:
        for folder in directory.iterdir():
            if not folder.is_dir() or folder.is_symlink():
                continue
            try:
                uuid.UUID(folder.name)
                meta = folder / "session.json"
                if meta.is_file() and meta.stat().st_mtime < cutoff:
                    import shutil
                    sessions.pop(folder.name, None)
                    shutil.rmtree(folder)
            except (OSError, ValueError):
                continue


def register(app: Any, ctx: dict[str, Any]) -> None:
    """Extend original v1 app while reusing its single shared Whisper CPU worker."""
    def job_create(kind: str, name: str, model: str, language: str, **data: Any) -> str:
        with ctx["lock"]:
            outstanding = sum(j["status"] in {"uploading", "queued", "running"}
                              and (j.get("kind") == "live") == (kind == "live")
                              for j in ctx["jobs"].values())
            cap = 8 if kind == "live" else ctx["MAX_OUTSTANDING"]
            if outstanding >= cap:
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
             "completed": 0, "failed": 0, "dropped": 0, "last_error": None}
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

    @app.delete("/api/live/{sid}")
    def delete_live(
        sid: str,
        x_local_whisper_client: str | None = Header(None, alias="X-Local-Whisper-Client"),
    ) -> dict[str, bool]:
        check_header(x_local_whisper_client)
        get_session(sid)
        with ctx["lock"]:
            pending = any(j.get("session_id") == sid and
                          j["status"] in {"uploading", "queued", "running"}
                          for j in ctx["jobs"].values())
        if pending:
            raise HTTPException(409, "仍有待处理片段，无法删除会话")
        with SESSION_LOCK:
            if sessions[sid]["status"] != "stopped":
                raise HTTPException(409, "请先停止监听")
            sessions.pop(sid, None)
            import shutil
            shutil.rmtree(session_location(sid), ignore_errors=True)
        return {"deleted": True}

    @app.get("/api/live/{sid}/download/{fmt}")
    def download_live(sid: str, fmt: str) -> Response:
        if fmt not in {"txt", "srt", "vtt", "json"}:
            raise HTTPException(404, "不支持的格式")
        s = get_session(sid)
        segments = sorted(s["segments"], key=lambda v: (v["start"], v["end"]))
        filename = f"live-{sid[:8]}.{fmt}"
        if fmt == "srt":
            content = to_srt(segments)
        elif fmt == "vtt":
            content = to_vtt(segments)
        elif fmt == "json":
            content = json.dumps({"session_id": sid, "segments": segments}, ensure_ascii=False, indent=2)
        else:
            content = "\n".join(x["text"] for x in segments) + ("\n" if segments else "")
        media_type = "application/json" if fmt == "json" else "text/plain; charset=utf-8"
        return Response(content, media_type=media_type,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/api/live/{sid}/gaps", status_code=202)
    def record_gap(sid: str, gap: GapRequest,
                   x_local_whisper_client: str | None = Header(None, alias="X-Local-Whisper-Client")) -> dict[str, int]:
        """Mark an unsent client clip visibly in the persistent SRT timeline."""
        check_header(x_local_whisper_client)
        get_session(sid)
        with SESSION_LOCK:
            session = sessions[sid]
            if session["status"] != "monitoring":
                raise HTTPException(409, "监听已经停止")
            if session.get("dropped", 0) >= 1000:
                raise HTTPException(429, "本次会话未提交片段数量过多")
            start = gap.offset_seconds
            end = min(86400.0, start + gap.duration_seconds)
            session["segments"].append({
                "start": round(start, 3), "end": round(end, 3),
                "text": f"[未转写 · {start:.1f}–{end:.1f}s]", "kind": "gap"})
            session["segments"].sort(key=lambda item: (item["start"], item["end"]))
            session["dropped"] = session.get("dropped", 0) + 1
            persist_session(session)
            return {"dropped": session["dropped"]}

    def run_live(jid: str, file: Path, model: str, language: str, sid: str, offset: float, duration: float) -> None:
        try:
            ctx["update"](jid, status="running", stage="监听片段转写中")
            actual_duration = ctx["probe_audio"](file)
            if abs(actual_duration - duration) > 0.6:
                raise ValueError("片段音频时长与客户端声明不一致")
            engine = ctx["load_model"](model)
            result = decode_file(engine, file, ctx["LANGUAGE_CHOICES"][language], live=True)
            segments = simple_segments(result, offset=offset, duration=actual_duration)
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
            ctx["executor"].submit(run_live, jid, path, s["model"], s["language"], sid, offset_seconds, duration_seconds, priority=0)
            return {"id": jid, "status": "queued"}
        except Exception:
            path.unlink(missing_ok=True)
            ctx["update"](jid, status="failed", stage="上传失败")
            raise
        finally:
            await file.close()

    def run_batch(jid: str, files: list[Path], inp: Path, out: Path, model: str, lang: str, skip_existing: bool) -> None:
        # One MP3 per task lets live and upload requests preempt the *next* file,
        # while keeping inference strictly serialized on low-end CPUs.
        state: dict[str, Any] = {"index": 0, "ok": 0, "fail": 0,
                                 "skipped": 0, "no_speech": 0, "detail": []}

        def step() -> None:
            idx = state["index"]
            if idx >= len(files):
                failures = state["fail"]
                status = "completed_with_errors" if failures and (
                    state["ok"] or state["skipped"] or state["no_speech"]) else (
                        "failed" if failures else "complete")
                ctx["update"](jid, status=status, stage="批量处理结束",
                              completed_files=state["ok"], failed_files=failures,
                              skipped_files=state["skipped"], no_speech_files=state["no_speech"],
                              errors=state["detail"][:100])
                return
            source = files[idx]
            rel = source.relative_to(inp).with_suffix(".srt")
            destination = out / rel
            state["index"] += 1
            ctx["update"](jid, status="running", stage=f"[{idx + 1}/{len(files)}] {source.name}",
                          completed_files=state["ok"], failed_files=state["fail"],
                          skipped_files=state["skipped"], no_speech_files=state["no_speech"])
            try:
                if not source.resolve().is_relative_to(inp):
                    raise ValueError("文件链接指向扫描目录之外")
                destination.parent.mkdir(parents=True, exist_ok=True)
                marker = destination.with_name(destination.name + ".no-speech")
                if not safe_output(destination, out) or not safe_output(marker, out):
                    raise ValueError("输出路径包含不安全的符号链接")
                if skip_existing and (destination.exists() or marker.exists()):
                    state["skipped"] += 1
                else:
                    if source.stat().st_size > ctx["MAX_BYTES"]:
                        raise ValueError("文件超过单文件大小限制")
                    ctx["probe_audio"](source)
                    engine = ctx["load_model"](model)
                    result = decode_file(engine, source, ctx["LANGUAGE_CHOICES"][lang])
                    segments = simple_segments(result)
                    if segments:
                        atomic_text(destination, to_srt(segments))
                        marker.unlink(missing_ok=True)
                        state["ok"] += 1
                    else:
                        atomic_text(marker, "No speech segments detected; no SRT generated.\n")
                        state["no_speech"] += 1
            except Exception as exc:
                state["fail"] += 1
                state["detail"].append({"file": str(rel), "error": str(exc)[:200]})
            ctx["executor"].submit(step, priority=20)

        ctx["executor"].submit(step, priority=20)

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
        ctx["executor"].submit(run_batch, jid, files, inp, out, request.model, request.language, request.skip_existing, priority=20)
        return {"id": jid, "total_files": len(files), "output_directory": str(out)}

    @app.post("/api/batch/preview")
    def preview_batch(request: BatchRequest, x_local_whisper_client: str | None = Header(None, alias="X-Local-Whisper-Client")) -> dict[str, Any]:
        check_header(x_local_whisper_client)
        inp = safe_directory(request.input_directory)
        files = list_mp3(inp, request.recursive)
        return {"count": len(files), "files": [str(f.relative_to(inp)) for f in files[:40]]}

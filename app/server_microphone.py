"""Capture the Ubuntu host microphone and submit speech clips to the live queue."""
import hmac
import logging
import os
import subprocess
from threading import Event, Lock, Thread, current_thread
from typing import Callable

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from .sound_gate import Clip, FRAME_SAMPLES, SAMPLE_RATE, SoundGate

logger = logging.getLogger("whisper_local")
MAX_LISTEN_SECONDS = 7200


class ServerMicrophone:
    """Own one local arecord process; state is mutable across HTTP requests."""

    def __init__(self, create_session: Callable[[str, str], str],
                 submit_clip: Callable[[str, Clip], None], stop_session: Callable[[str], None]) -> None:
        self.create_session = create_session
        self.submit_clip = submit_clip
        self.stop_session = stop_session
        self.lock = Lock()
        self.stop_event = Event()
        self.recalibrate_event = Event()
        self.process: subprocess.Popen[bytes] | None = None
        self.thread: Thread | None = None
        self.session_id: str | None = None
        self.state = "stopped"
        self.level_db = -100.0
        self.noise_db = -65.0
        self.elapsed_seconds = 0.0
        self.error: str | None = None

    def start(self, model: str, language: str) -> str:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise HTTPException(409, "主机麦克风已在监听")
            command = ["arecord", "-q", "-D", os.getenv("WHISPER_CAPTURE_DEVICE", "default"),
                       "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1", "-t", "raw", "-"]
            try:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except OSError as exc:
                raise HTTPException(503, f"无法启动主机麦克风: {exc}") from exc
            try:
                sid = self.create_session(model, language)
            except (HTTPException, OSError):
                process.terminate()
                process.wait(timeout=3)
                raise
            self.stop_event = Event()
            self.recalibrate_event = Event()
            self.process = process
            self.session_id = sid
            self.state = "calibrating"
            self.error = None
            self.level_db = -100.0
            self.elapsed_seconds = 0.0
            self.thread = Thread(target=self._capture, args=(sid, process), name="whisper-host-mic", daemon=True)
            self.thread.start()
            return sid

    def _capture(self, sid: str, process: subprocess.Popen[bytes]) -> None:
        gate = SoundGate(lambda clip: self.submit_clip(sid, clip))
        try:
            assert process.stdout is not None
            while not self.stop_event.is_set():
                frame = process.stdout.read(FRAME_SAMPLES * 2)
                if not frame:
                    if not self.stop_event.is_set() and process.poll() != 0:
                        stderr = process.stderr.read(300) if process.stderr else b""
                        raise OSError(stderr.decode(errors="replace") or "录音程序意外结束")
                    break
                if self.recalibrate_event.is_set():
                    gate.recalibrate()
                    self.recalibrate_event.clear()
                gate.push(frame)
                with self.lock:
                    self.state = gate.state
                    self.level_db = gate.level_db
                    self.noise_db = gate.noise_db
                    self.elapsed_seconds = gate.samples_seen / SAMPLE_RATE
                if gate.samples_seen >= SAMPLE_RATE * MAX_LISTEN_SECONDS:
                    break
            gate.flush()
        except Exception as exc:  # noqa: BROAD_EXCEPT_OK - capture thread boundary
            logger.exception("server microphone capture failed")
            with self.lock:
                self.error = str(exc)[:240]
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
            self.stop_session(sid)
            with self.lock:
                self.process = None
                self.state = "failed" if self.error else "stopped"

    def stop(self) -> None:
        with self.lock:
            self.stop_event.set()
            process = self.process
            thread = self.thread
        if process is not None and process.poll() is None:
            process.terminate()
        if thread is not None and thread is not current_thread():
            thread.join(timeout=5)

    def recalibrate(self) -> None:
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                raise HTTPException(409, "主机麦克风未在监听")
            self.recalibrate_event.set()

    def status(self) -> dict[str, str | float | None]:
        with self.lock:
            return {"status": self.state, "session_id": self.session_id,
                    "level_db": round(self.level_db, 1), "noise_db": round(self.noise_db, 1),
                    "threshold_db": round(min(-12.0, max(-56.0, self.noise_db + 12.0)), 1),
                    "elapsed_seconds": round(self.elapsed_seconds, 1), "error": self.error}


class StartRequest(BaseModel):
    model: str = "tiny"
    language: str = "zh"


def register(app: FastAPI, create_session: Callable[[str, str], str],
             submit_clip: Callable[[str, Clip], None], stop_session: Callable[[str], None]) -> None:
    microphone = ServerMicrophone(create_session, submit_clip, stop_session)
    app.state.server_microphone = microphone

    def authorize(token: str | None) -> None:
        expected = os.getenv("WHISPER_HOST_MIC_TOKEN", "")
        if not expected:
            raise HTTPException(503, "未配置主机麦克风控制口令")
        if token is None or not hmac.compare_digest(token, expected):
            raise HTTPException(403, "主机麦克风控制口令不正确")

    @app.post("/api/server-mic/start", status_code=201)
    def start_microphone(request: StartRequest,
                         token: str | None = Header(None, alias="X-Whisper-Mic-Token")) -> dict[str, str]:
        authorize(token)
        return {"id": microphone.start(request.model, request.language)}

    @app.post("/api/server-mic/stop")
    def stop_microphone(token: str | None = Header(None, alias="X-Whisper-Mic-Token")) -> dict[str, str]:
        authorize(token)
        microphone.stop()
        return {"status": microphone.status()["status"] or "stopped"}

    @app.post("/api/server-mic/calibrate")
    def calibrate_microphone(token: str | None = Header(None, alias="X-Whisper-Mic-Token")) -> dict[str, str]:
        authorize(token)
        microphone.recalibrate()
        return {"status": "calibrating"}

    @app.get("/api/server-mic/status")
    def microphone_status(token: str | None = Header(None, alias="X-Whisper-Mic-Token")) -> dict[str, str | float | None]:
        authorize(token)
        return microphone.status()

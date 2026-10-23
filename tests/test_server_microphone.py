"""Host microphone sound gate regression tests."""
import unittest
import os
import tempfile
import time
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.sound_gate import Clip, FRAME_SAMPLES, SoundGate
from app import main


class SoundGateTests(unittest.TestCase):
    def test_sustained_speech_emits_timed_clip_after_silence(self):
        clips: list[Clip] = []
        gate = SoundGate(clips.append)
        silence = b"\0\0" * FRAME_SAMPLES
        speech = (12000).to_bytes(2, "little", signed=True) * FRAME_SAMPLES

        for _ in range(14):
            gate.push(silence)
        for _ in range(5):
            gate.push(speech)
        for _ in range(13):
            gate.push(silence)

        self.assertEqual(len(clips), 1)
        self.assertAlmostEqual(clips[0].offset_seconds, 1.4)
        self.assertAlmostEqual(clips[0].duration_seconds, 1.7)
        self.assertEqual(len(clips[0].pcm), 17 * FRAME_SAMPLES * 2)

    def test_host_microphone_submits_speech_to_live_session(self):
        silence = b"\0\0" * FRAME_SAMPLES
        speech = (12000).to_bytes(2, "little", signed=True) * FRAME_SAMPLES

        class CaptureProcess:
            def __init__(self):
                self.stdout = BytesIO(silence * 14 + speech * 5 + silence * 13)
                self.stderr = BytesIO()

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

            def terminate(self):
                pass

            def kill(self):
                pass

        class Model:
            def transcribe(self, filename, **options):
                return {"language": "zh", "text": "因為我聽過",
                        "segments": [{"start": 0.2, "end": 1.1, "text": "因為我聽過"}]}

        with tempfile.TemporaryDirectory() as root, \
             patch.dict(os.environ, {"WHISPER_HOST_MIC_TOKEN": "test-token"}), \
             patch.object(main, "DATA_ROOT", Path(root)), \
             patch.object(main, "load_model", return_value=Model()), \
             patch.object(main, "probe_audio", return_value=1.7), \
             patch("app.server_microphone.subprocess.Popen", return_value=CaptureProcess()):
            main.jobs.clear()
            with TestClient(main.app) as client:
                headers = {"X-Whisper-Mic-Token": "test-token"}
                started = client.post("/api/server-mic/start", headers=headers,
                                      json={"model": "tiny", "language": "zh"})
                self.assertEqual(started.status_code, 201, started.text)
                sid = started.json()["id"]
                for _ in range(100):
                    session = client.get(f"/api/live/{sid}").json()
                    if session["completed"]:
                        break
                    time.sleep(.03)
                self.assertEqual(session["completed"], 1, session)
                self.assertEqual(session["status"], "stopped")
                self.assertIn("00:00:01,600", client.get(f"/api/live/{sid}/download/srt").text)
                self.assertIn("因为我听过", client.get(f"/api/live/{sid}/download/srt").text)


if __name__ == "__main__":
    unittest.main()

"""Sound activity segmentation for host microphone PCM samples."""
import math
from collections import deque
from dataclasses import dataclass
from typing import Callable

import numpy as np

SAMPLE_RATE = 16000
FRAME_SAMPLES = 1600


@dataclass(frozen=True, slots=True)
class Clip:
    pcm: bytes
    offset_seconds: float
    duration_seconds: float


class SoundGate:
    """Split a 16 kHz mono PCM stream after sustained sound and silence."""

    def __init__(self, on_clip: Callable[[Clip], None]) -> None:
        self.on_clip = on_clip
        self.samples_seen = 0
        self.calibration_end = int(SAMPLE_RATE * 1.4)
        self.calibration_levels: list[float] = []
        self.noise_db = -65.0
        self.level_db = -100.0
        self.state = "calibrating"
        self.history: deque[tuple[int, bytes]] = deque(maxlen=4)
        self.frames: list[tuple[int, bytes]] = []
        self.pending_above = 0
        self.silent_samples = 0
        self.active = False

    def threshold(self) -> float:
        return min(-12.0, max(-56.0, self.noise_db + 12.0))

    def push(self, frame: bytes) -> None:
        samples = np.frombuffer(frame, dtype="<i2").astype(np.float32)
        if not samples.size:
            return
        self.level_db = 20.0 * math.log10(float(np.sqrt(np.mean(samples * samples))) / 32768.0 + 1e-9)
        begin = self.samples_seen
        self.samples_seen += samples.size
        if self.samples_seen <= self.calibration_end:
            self.calibration_levels.append(self.level_db)
            levels = sorted(self.calibration_levels)
            self.noise_db = levels[int((len(levels) - 1) * 0.2)]
            self.state = "calibrating"
            return
        if not self.active:
            self.history.append((begin, frame))
            above = self.level_db >= self.threshold()
            self.pending_above = self.pending_above + samples.size if above else max(0, self.pending_above - samples.size // 2)
            if self.pending_above >= int(SAMPLE_RATE * 0.18):
                self.active = True
                self.frames = list(self.history)
                self.history.clear()
                self.pending_above = 0
                self.silent_samples = 0
                self.state = "recording"
            else:
                if not above:
                    self.noise_db = 0.98 * self.noise_db + 0.02 * self.level_db
                self.state = "silent"
            return
        self.frames.append((begin, frame))
        self.silent_samples = self.silent_samples + samples.size if self.level_db < self.threshold() - 4 else 0
        if self.silent_samples >= int(SAMPLE_RATE * 1.2):
            self.flush()
        elif self.samples_seen - self.frames[0][0] >= SAMPLE_RATE * 22:
            self.flush(split=True)

    def flush(self, *, split: bool = False) -> None:
        if not self.active or not self.frames:
            return
        frames = self.frames
        duration = sum(len(data) // 2 for _, data in frames) / SAMPLE_RATE
        if duration >= 0.35:
            self.on_clip(Clip(b"".join(data for _, data in frames), frames[0][0] / SAMPLE_RATE, duration))
        self.frames = frames[-3:] if split else []
        self.active = split
        self.silent_samples = 0
        self.state = "recording" if split else "silent"

    def recalibrate(self) -> None:
        self.calibration_end = self.samples_seen + int(SAMPLE_RATE * 1.4)
        self.calibration_levels = []
        self.history.clear()
        self.frames = []
        self.active = False
        self.pending_above = 0
        self.silent_samples = 0
        self.state = "calibrating"

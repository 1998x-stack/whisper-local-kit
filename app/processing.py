"""Shared ASR decoding, segment metadata and atomic writes, without web dependencies."""
import os
import uuid
from pathlib import Path
from typing import Any


def decode_file(model: Any, path: Path, language: str | None, *, task: str = "transcribe", live: bool = False) -> dict[str, Any]:
    # beam_size=1 still selects beam search; None uses greedy decoding.
    # Temperature fallback is worthwhile on longer files, not real-time clips.
    options: dict[str, Any] = dict(
        language=language, task=task, fp16=False, verbose=None,
        temperature=0.0 if live else (0.0, 0.2, 0.4),
        beam_size=None, best_of=1, condition_on_previous_text=False,
        word_timestamps=False,
    )
    prompt = os.getenv("WHISPER_ZH_INITIAL_PROMPT", "").strip()
    if language == "zh" and task == "transcribe" and prompt:
        options["initial_prompt"] = prompt[:300]
    return model.transcribe(str(path), **options)


def simple_segments(result: dict[str, Any], offset: float = 0.0, duration: float | None = None,
                    filter_low_confidence: bool | None = None) -> list[dict[str, Any]]:
    """Apply conservative silence filtering while retaining confidence fields.

    Never exclude a segment on no_speech_prob alone: the score is not calibrated
    for all microphones, accents or engines, and the JSON assists manual review.
    """
    enabled = (os.getenv("WHISPER_FILTER_LOW_CONFIDENCE", "1") == "1"
               if filter_low_confidence is None else filter_low_confidence)
    out: list[dict[str, Any]] = []
    for item in result.get("segments", []):
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        noise, logprob = item.get("no_speech_prob"), item.get("avg_logprob")
        if (enabled
                and isinstance(noise, (float, int)) and isinstance(logprob, (float, int))
                and noise >= 0.85 and logprob < -1.2):
            continue
        start = max(0.0, float(item.get("start", 0)))
        end = max(start, float(item.get("end", start)))
        if duration is not None:
            start, end = min(start, duration), min(end, duration)
        if end <= start:
            continue
        segment: dict[str, Any] = {
            "start": round(start + offset, 3),
            "end": round(end + offset, 3),
            "text": text,
        }
        for key in ("no_speech_prob", "avg_logprob", "compression_ratio"):
            if isinstance(item.get(key), (float, int)):
                segment[key] = round(float(item[key]), 5)
        out.append(segment)
    return out


def transcript_text(result: dict[str, Any], segments: list[dict[str, Any]]) -> str:
    original = [s for s in result.get("segments", []) if str(s.get("text", "")).strip()]
    if len(original) == len(segments):
        return str(result.get("text", "")).strip() or " ".join(s["text"] for s in segments)
    return " ".join(s["text"] for s in segments)


def atomic_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)

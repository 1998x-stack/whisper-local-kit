"""Minimal, dependency-free subtitle formatting for Whisper segment data."""
from typing import Any


def timestamp(seconds: float, vtt: bool = False) -> str:
    milliseconds = max(0, int(round(float(seconds) * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    separator = "." if vtt else ","
    return f"{hours:02}:{minutes:02}:{secs:02}{separator}{millis:03}"


def to_srt(segments: list[dict[str, Any]]) -> str:
    lines = []
    for number, segment in enumerate(segments, start=1):
        lines.append(str(number))
        lines.append(f"{timestamp(segment['start'])} --> {timestamp(segment['end'])}")
        lines.append(str(segment['text']).strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n" if lines else ""


def to_vtt(segments: list[dict[str, Any]]) -> str:
    lines = ["WEBVTT", ""]
    for segment in segments:
        lines.append(f"{timestamp(segment['start'], True)} --> {timestamp(segment['end'], True)}")
        lines.append(str(segment['text']).strip())
        lines.append("")
    return "\n".join(lines)

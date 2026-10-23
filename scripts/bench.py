#!/usr/bin/env python3
"""Reproducible local Whisper CPU baseline; outputs no transcript/audio data."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import platform
import sys
import statistics
import subprocess
import time


def audio_duration(path: Path) -> float:
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                           "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                          check=True, capture_output=True, text=True, timeout=30)
    value = float(proc.stdout.strip())
    if not 0 < value <= 86400:
        raise ValueError("Audio duration is missing or out of range")
    return value


def cpu_features() -> dict[str, bool]:
    flags = ""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith(("flags", "features")):
                flags = line.partition(":")[2]
                break
    except OSError:
        pass
    names = set(flags.split())
    return {key: key in names for key in ("avx", "avx2", "fma", "sse4_2")}


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark openai-whisper on a real CPU")
    parser.add_argument("--audio", required=True, type=Path, help="Local audio to transcribe")
    parser.add_argument("--models", nargs="+", default=["tiny", "base"],
                        choices=["tiny", "base", "small"])
    parser.add_argument("--language", default="zh", help="Use auto for language detection")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", type=Path, default=Path("whisper-bench.json"))
    args = parser.parse_args()
    if not 1 <= args.repeat <= 10:
        parser.error("--repeat must be between 1 and 10")
    path = args.audio.expanduser().resolve()
    if not path.is_file():
        parser.error(f"Audio does not exist: {path}")
    duration = audio_duration(path)
    import torch
    import whisper
    from app.processing import decode_file
    threads = max(1, min(8, int(os.getenv("WHISPER_THREADS", "2"))))
    torch.set_num_threads(threads)
    cache = str(Path(os.getenv("WHISPER_MODEL_DIR", str(Path.home() / ".cache/whisper"))).expanduser())
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine": "openai-whisper",
        "platform": platform.platform(),
        "processor": platform.processor(),
        "features": cpu_features(),
        "python": platform.python_version(),
        "torch": torch.__version__,
        "threads": threads,
        "audio_duration_seconds": round(duration, 3),
        "model_results": [],
    }
    for name in args.models:
        loading = time.perf_counter()
        model = whisper.load_model(name, device="cpu", download_root=cache)
        load_sec = time.perf_counter() - loading
        elapsed = []
        segment_counts = []
        for attempt in range(args.repeat):
            start = time.perf_counter()
            result = decode_file(model, path, None if args.language == "auto" else args.language)
            runtime = time.perf_counter() - start
            elapsed.append(runtime)
            segment_counts.append(len(result.get("segments", [])))
            print(f"{name} pass {attempt+1}/{args.repeat}: {runtime:.2f}s, RTF={runtime/duration:.3f}", flush=True)
        summary["model_results"].append({
            "model": name,
            "model_load_seconds": round(load_sec, 3),
            "runs_seconds": [round(x, 3) for x in elapsed],
            "rtf_median": round(statistics.median(elapsed) / duration, 4),
            "segments_per_run": segment_counts,
        })
        del model
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(f"Benchmark JSON: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

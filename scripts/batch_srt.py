#!/usr/bin/env python3
"""Independent CPU-only audio-folder -> SRT CLI.

Uses the same decoding, confidence filtering, model location and limits as the
web service but does not import FastAPI. Original media is never deleted.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.subtitles import to_srt
from app.processing import atomic_text, simple_segments, decode_file

MAX_FILES = 300
MAX_ENTRIES = 20000
MAX_BYTES = int(os.getenv("WHISPER_MAX_UPLOAD_MB", "200")) * 1048576
MAX_DURATION = int(os.getenv("WHISPER_MAX_DURATION_SECONDS", "7200"))
SUFFIXES = {".mp3"}


def natural_key(value: Path):
    return [int(v) if v.isdigit() else v.casefold()
            for v in re.split(r"(\d+)", str(value))]


def find_audio(folder: Path, recursive: bool) -> list[Path]:
    found = []
    for scanned, source in enumerate(folder.rglob("*") if recursive else folder.iterdir(), 1):
        if scanned > MAX_ENTRIES:
            raise ValueError(f"扫描超过 {MAX_ENTRIES} 个条目")
        if source.suffix.lower() in SUFFIXES and source.is_file() and source.resolve().is_relative_to(folder):
            found.append(source)
            if len(found) > MAX_FILES:
                raise ValueError(f"每次最多 {MAX_FILES} 个音频文件")
    return sorted(found, key=natural_key)


def probe(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if proc.returncode:
        raise ValueError("ffprobe 无法读取音频")
    data = json.loads(proc.stdout)
    if not any(s.get("codec_type") == "audio" for s in data.get("streams", [])):
        raise ValueError("音频流不存在")
    duration = float(data.get("format", {}).get("duration", "nan"))
    if not 0 < duration <= MAX_DURATION:
        raise ValueError("文件时长无效或超过限制")
    return duration


def main() -> int:
    cli = argparse.ArgumentParser(description="Whisper Local MP3 folder to SRT (CPU)")
    cli.add_argument("--input", required=True)
    cli.add_argument("--output", help="Default is source directory")
    cli.add_argument("--model", choices=["tiny", "base", "small"], default="tiny")
    cli.add_argument("--language", choices=["zh", "auto", "en", "ja", "ko", "fr", "de", "es"], default="zh")
    cli.add_argument("--recursive", action="store_true")
    cli.add_argument("--overwrite", action="store_true")
    cli.add_argument("--dry-run", action="store_true")
    args = cli.parse_args()
    inp = Path(args.input).expanduser().resolve()
    out = Path(args.output).expanduser().resolve() if args.output else inp
    if not inp.is_dir():
        cli.error(f"输入文件夹不存在: {inp}")
    try:
        files = find_audio(inp, args.recursive)
    except ValueError as exc:
        cli.error(str(exc))
    print(f"找到 {len(files)} 个 MP3，输出根目录：{out}")
    if args.dry_run:
        for source in files:
            print(f"{source} → {out / source.relative_to(inp).with_suffix('.srt')}")
        return 0
    if not files:
        return 0

    import torch
    import whisper
    threads = max(1, min(8, int(os.getenv("WHISPER_THREADS", "2"))))
    torch.set_num_threads(threads)
    model_dir = Path(os.getenv("WHISPER_MODEL_DIR", str(Path.home() / ".cache/whisper"))).expanduser()
    model = whisper.load_model(args.model, device="cpu", download_root=str(model_dir))
    ok = skipped = failed = no_speech = 0
    for i, source in enumerate(files, 1):
        destination = out / source.relative_to(inp).with_suffix(".srt")
        marker = destination.with_name(destination.name + ".no-speech")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if (not destination.parent.resolve().is_relative_to(out)
                    or destination.is_symlink() or marker.is_symlink()):
                raise ValueError("输出路径存在符号链接风险")
            if not args.overwrite and (destination.exists() or marker.exists()):
                skipped += 1
                print(f"[{i}/{len(files)}] 跳过已有: {destination}")
                continue
            if source.stat().st_size > MAX_BYTES:
                raise ValueError("文件超过配置大小限制")
            probe(source)
            print(f"[{i}/{len(files)}] 转写: {source}", flush=True)
            result = decode_file(model, source, None if args.language == "auto" else args.language)
            segments = simple_segments(result)
            if segments:
                atomic_text(destination, to_srt(segments))
                marker.unlink(missing_ok=True)
                ok += 1
            else:
                atomic_text(marker, "No speech segments detected; no SRT generated.\n")
                no_speech += 1
        except Exception as exc:
            failed += 1
            print(f"失败: {source.name}: {exc}", file=sys.stderr)
    print(f"完成={ok} / 无语音={no_speech} / 跳过={skipped} / 失败={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

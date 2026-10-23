#!/usr/bin/env python3
"""Independent CPU-only MP3→SRT CLI; no API key, skip-existing by default."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.subtitles import to_srt
from app.extensions import atomic_text, simple_segments


def main() -> int:
    cli = argparse.ArgumentParser(description="Whisper Local MP3 folder to same-basename SRT")
    cli.add_argument("--input", required=True, help="MP3 directory on this Ubuntu machine")
    cli.add_argument("--output", help="Optional SRT output directory; default input directory")
    cli.add_argument("--model", choices=["tiny", "base", "small"], default="tiny")
    cli.add_argument("--language", choices=["zh", "auto", "en", "ja", "ko", "fr", "de", "es"], default="zh")
    cli.add_argument("--recursive", action="store_true")
    cli.add_argument("--overwrite", action="store_true", help="Overwrite existing SRT files")
    cli.add_argument("--dry-run", action="store_true")
    args = cli.parse_args()
    inp = Path(args.input).expanduser().resolve()
    out = Path(args.output).expanduser().resolve() if args.output else inp
    if not inp.is_dir():
        cli.error(f"输入文件夹不存在: {inp}")
    files = [p for p in (inp.rglob("*") if args.recursive else inp.iterdir())
             if p.is_file() and p.suffix.lower() == ".mp3" and p.resolve().is_relative_to(inp)]
    files.sort(key=lambda p: str(p).casefold())
    if not files:
        print("没有找到 MP3 文件"); return 0
    print(f"找到 {len(files)} 个 MP3，输出根目录：{out}")
    if args.dry_run:
        for p in files:
            print(f"{p} → {out / p.relative_to(inp).with_suffix('.srt')}")
        return 0
    import torch
    import whisper
    torch.set_num_threads(2)
    engine = whisper.load_model(args.model, device="cpu")
    ok=failed=skipped=0
    for i, p in enumerate(files, 1):
        destination = out / p.relative_to(inp).with_suffix(".srt")
        try:
            if not args.overwrite and destination.exists():
                skipped+=1; print(f"[{i}/{len(files)}] 跳过已有: {destination}"); continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.parent.resolve().is_relative_to(out) or destination.is_symlink():
                raise ValueError("输出路径存在符号链接风险")
            print(f"[{i}/{len(files)}] 正在转写: {p}", flush=True)
            result=engine.transcribe(str(p), language=None if args.language=="auto" else args.language,
                                     fp16=False, verbose=None, temperature=0, beam_size=1, best_of=1)
            atomic_text(destination, to_srt(simple_segments(result)))
            ok+=1
        except Exception as e:
            failed+=1; print(f"失败: {p.name}: {e}", file=sys.stderr)
    print(f"完成={ok} / 跳过={skipped} / 失败={failed}")
    return 1 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoCut Music — подложка под рыбу: loop/trim/fade + дакинг под речь.

Дакинг честный, без магии: музыка пригибается там, где по speech.json
(из interview.py) идёт речь, и возвращается в паузах. Для дайджеста без
транскрипта — ровная тихая подложка с фейдами (дакить не под что).

Использование:
  # интервью: дакинг по речи
  python music.py --music трек.mp3 --duration 84.5 --duck rz/speech.json --out bed.wav
  # дайджест: ровная подложка
  python music.py --music трек.mp3 --duration 60 --out bed.wav
  # дальше подложка ложится в Premiere сама:
  python premiere.py --timeline tl.csv --media-dir . --out ryba.xml --music bed.wav

Уровни: --bed-db (-20) громкость подложки в паузах, --duck-db (-32) под речью.
"""

from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def probe_dur(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def build_filter(speech: list, total: float, bed_db: float, duck_db: float,
                 fade: float) -> str:
    """Фильтр: нарезка на куски речь/пауза + свой уровень + склейка + фейды."""
    import math
    bed = 10 ** (bed_db / 20)
    duck = 10 ** (duck_db / 20)
    bounds = sorted({0.0} | {round(max(0.0, min(total, s["start"])), 3) for s in speech} |
                    {round(max(0.0, min(total, s["end"])), 3) for s in speech} | {total})

    def is_speech(a, b):
        mid = (a + b) / 2
        return any(s["start"] <= mid <= s["end"] for s in speech)

    parts, maps = [], []
    for k, (a, b) in enumerate(zip(bounds, bounds[1:])):
        if b - a < 0.02:
            continue
        vol = duck if is_speech(a, b) else bed
        parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
                     f"volume={vol:.4f}[p{k}]")
        maps.append(f"[p{k}]")
    n = len(maps)
    tail = f"{''.join(maps)}concat=n={n}:v=0:a=1,aresample=48000,aformat=channel_layouts=stereo"
    if fade > 0:
        f = min(fade, total / 4)
        tail += f",afade=t=in:st=0:d={f:.2f},afade=t=out:st={total - f:.2f}:d={f:.2f}"
    return ";".join(parts) + ";" + tail


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AutoCut Music — подложка с дакингом")
    ap.add_argument("--music", required=True, help="трек-подложка (mp3/wav)")
    ap.add_argument("--duration", type=float, required=True, help="хронометраж секвенции (сек)")
    ap.add_argument("--duck", default="", help="speech.json из interview.py (дакинг под речь)")
    ap.add_argument("--out", default="bed.wav", help="выход (48кГц стерео wav)")
    ap.add_argument("--bed-db", type=float, default=-20.0, help="уровень в паузах")
    ap.add_argument("--duck-db", type=float, default=-32.0, help="уровень под речью")
    ap.add_argument("--fade", type=float, default=2.0, help="фейды вх/вых (сек)")
    args = ap.parse_args(argv)

    music = Path(args.music)
    if not music.is_file():
        print(f"Не найден трек: {music}")
        return 2
    if args.duck and not Path(args.duck).is_file():
        print(f"Не найден speech.json: {args.duck} (его делает interview.py). Продолжаю без дакинга.")
        args.duck = ""
    total = args.duration
    if total <= 0:
        print("duration должен быть > 0.")
        return 1
    mlen = probe_dur(music)
    loops = max(0, int(total // mlen))
    speech = []
    if args.duck:
        speech = json.loads(Path(args.duck).read_text(encoding="utf-8"))
        print(f"Речевых интервалов для дакинга: {len(speech)}")
    else:
        print("Без --duck: ровная подложка.")

    filt = build_filter(speech, total, args.bed_db, args.duck_db, args.fade)
    cmd = ["ffmpeg", "-y", "-v", "error", "-stream_loop", str(loops), "-i", str(music),
           "-filter_complex", filt, "-t", f"{total:.2f}", "-ar", "48000", "-ac", "2",
           "-c:a", "pcm_s16le", str(args.out)]
    subprocess.run(cmd, check=True)
    got = probe_dur(Path(args.out))
    print(f"Подложка: {args.out} ({got:.1f}с из {total:.1f}с). "
          f"Дальше: premiere.py --music {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

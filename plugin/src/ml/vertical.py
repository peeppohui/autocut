#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Вертикальная версия готового монтажа: 9:16 для Reels и Shorts.

Что делает. Берёт уже собранный selects.csv и переделывает каждый
видеокусок в вертикальный: центр кадра вырезается по 9:16 и
растягивается до 1080x1920. На выходе - новая раскладка с путями на
вертикальные файлы, её и скармливаем premiere.py.

Почему через ffmpeg, а не кадрированием в XML. Кадрирование в FCP7
делается вложенным clipitem через clipref, и Premiere не гарантирует,
что читает это как «заполнить по центру» - а нечитает он молча, и
импорт падает целиком. Тот же класс граблей уже стоил четырех попыток
с <multiclip>. Здесь путь другой: XML собирается ровно так же, как
уже умеет, а кадр меняется на диске.

Почему это не портит оригиналы и не жрёт два часа перекодирования.
Перекодируются только куски МОНТАЖА, а не исходники: из двухчасового
интервью в вертикаль уходит ровно столько, сколько длится рыба.

Звук не трогаем: колонка audio указывает на отдельные микрофоны, резать
там нечего, пробегать их заново не нужно.
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bootstrap  # noqa: F401  — регистрирует src/config в sys.path

VIDEO_EXT = (".mp4", ".mov", ".mxf", ".mkv")


def crop_filter(width: int, height: int, speed: float = 1.0) -> str:
    """Обрезка по центру под 9:16 с последующим масштабированием.

    Ширина реза считается от высоты, а не наоборот: вертикаль получается
    всегда одного кадра, даже если исходник шире или уже 16:9. Делим на
    2, потому что libx264 требует чётные размеры, а 1080*9/16 = 607.5.
    Ускорение через setpts делается здесь же, чтобы не считать
    длительность руками.
    """
    vf = f"crop=trunc(ih*9/16/2)*2:ih,scale={width}:{height}"
    # любое значение, отличное от 1.0, уважаем буквально: если человек
    # написал 1.01 - он хотел ускорить на процент, а не «примерно как
    # есть». Бессмысленные значения (0 или меньше) просто игнорируем.
    if speed > 0 and speed != 1.0:
        vf += f",setpts={1.0 / speed:.6f}*PTS"
    return vf


def clip_duration(row: Dict) -> float:
    """Длительность куска. Сначала in/out, и только если их нет - колонка."""
    try:
        a, b = float(row.get("in") or ""), float(row.get("out") or "")
        if b > a:
            return b - a
    except ValueError:
        pass
    try:
        return float(row.get("duration") or 0)
    except ValueError:
        return 0.0


def cut_one(src: Path, dst: Path, row: Dict, width: int, height: int,
            speed: float) -> str:
    """Один кусок в вертикаль. Возвращает "" или текст ошибки.

    Порядок аргументов важен: сначала все входы (-i), потом выходные
    опции. Стоит поставить -vf между двумя -i, ffmpeg ругается
    «Option vf cannot be applied to input» и уходит с кодом -22.
    """
    dur = clip_duration(row)
    if dur <= 0.1:
        return "у куска нет длительности"
    audio = (row.get("audio") or "").strip()

    inputs = ["-ss", f"{max(0.0, float(row.get('in') or 0)):.3f}",
              "-t", f"{dur:.3f}", "-i", str(src)]
    if audio and Path(audio).is_file():
        # звук берём из микрофонной дорожки: в вертикали он идёт
        # отдельной дорожкой, а встроенный в видео не нужен
        inputs += ["-i", audio]
    output = ["-vf", crop_filter(width, height, speed),
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
              "-pix_fmt", "yuv420p"]
    if audio and Path(audio).is_file():
        output += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-shortest"]
    else:
        output += ["-an"]

    proc = subprocess.run(["ffmpeg", "-y", "-v", "error"] + inputs
                          + output + [str(dst)],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not dst.is_file() or dst.stat().st_size == 0:
        # молчать нельзя: иначе кусок просто исчезнет из монтажа
        err = (proc.stderr or "").strip().splitlines()
        return err[-1] if err else f"ffmpeg код {proc.returncode}"
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Вертикальная (9:16) версия готового монтажа")
    ap.add_argument("--selects", required=True, help="исходный selects.csv")
    ap.add_argument("--outdir", required=True, help="куда сложить вертикаль")
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--speed", type=float, default=1.0,
                    help="ускорение, 1.5 = в полтора раза быстрее")
    args = ap.parse_args(argv)

    src_csv = Path(args.selects)
    if not src_csv.is_file():
        print(f"Не найден selects: {src_csv}")
        return 2
    out_dir = Path(args.outdir)
    (out_dir / "clips").mkdir(parents=True, exist_ok=True)

    with open(src_csv, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]

    out_rows: List[Dict] = []
    cut = skipped = 0
    for i, row in enumerate(rows):
        new = dict(row)
        track = (row.get("track") or "V1").strip().upper()
        src = (row.get("path") or "").strip()
        is_video = track.startswith("V") and Path(src).is_file() \
            and Path(src).suffix.lower() in VIDEO_EXT
        if not is_video:
            # самостоятельные звуковые дорожки и второй ракурс не трогаем
            out_rows.append(new)
            continue
        name = f"clip{i:04d}.mp4"
        dst = out_dir / "clips" / name
        speed = args.speed if track.upper() == "V1" else 1.0
        err = cut_one(Path(src), dst, row, args.width, args.height, speed)
        if not err:
            new["path"] = str(dst)
            new["file"] = name
            new["duration"] = f"{clip_duration(row) / speed:.3f}"
            cut += 1
        else:
            # кусок оставляем горизонтальным и говорим почему: иначе в
            # вертикальном ролике будет зелёная полоса без объяснений
            skipped += 1
            print(f"  кусок {i} ({row.get('file')}) оставлен как есть: {err}")
        out_rows.append(new)

    out_csv = out_dir / "selects.csv"
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in out_rows:
            w.writerow({k: r.get(k, "") for k in fields})

    print(f"Вертикаль готова: {cut} кусков, пропущено {skipped}")
    print(f"  {args.width}x{args.height}"
          + (f", скорость x{args.speed}" if args.speed != 1.0 else ""))
    print(f"  {out_csv}")
    print(f"Дальше: premiere.py --timeline {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
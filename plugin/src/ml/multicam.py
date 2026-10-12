# -*- coding: utf-8 -*-
"""Подготовка камер к настоящему мультикаму в Premiere.

FCP7-XML так умеет, что Premiere собирает из него мультикам: там
элемент <multiclip> либо игнорируется, либо импортируется как обычные
клипы. Мы это проверили - построить мультикам из XML не выйдет.

Зато Premiere умеет строить мультикам САМ, если у файлов совпадает
таймкод: Project > правой кнопкой > Multi-Camera > Create
Multicam Sequence > Synchronize Point: Timecode.

Всё, что нужно, - впечатать в файлы одинаковый таймкод. Делается
одной командой ffmpeg с ключом -c copy: видео не перекодируется,
файл просто получает метку. Две камеры по гигабайту - меньше минуты.

Синхронизация при этом не нужна: таймкод кодирует СМЕЩЕНИЕ. У камер
исходники стартуют в разное время, значит и метки разные - Premiere
разложит их как надо. Важно только не сдвигать само видео.

Запуск:
    python -m src.ml.multicam --input synced/ref.mp4 synced/cam.mp4 \
                              --outdir multicam
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Таймкод по умолчанию. Час - не «магическое» число, а просто точка,
# с которой Premiere начинает отсчёт; важно, чтобы он был одинаков и
# не близок к нулю.
DEFAULT_TC = "01:00:00:00"


def parse_tc(text: str) -> Tuple[int, int, int, int]:
    """'01:00:00:00' -> (1, 0, 0, 0). False на мусоре."""
    parts = str(text or "").strip().split(":")
    if len(parts) != 4:
        return (0, 0, 0, 0)
    try:
        h, m, s, f = (int(float(p)) for p in parts)
    except ValueError:
        return (0, 0, 0, 0)
    return (h, m, s, f)


def tc_frames(text: str, fps: int = 25) -> int:
    h, m, s, f = parse_tc(text)
    if not (h or m or s or f):
        return 0
    return ((h * 60 + m) * 60 + s) * fps + f


def bake_timecode(src, dst, tc: str = DEFAULT_TC,
                  fps: int = 25) -> Dict:
    """Впечатывает таймкод в копию файла. Видео не перекодируется.

    Возвращает словарь: ok, путь, таймкод, кадр, размер, доля секунд.
    """
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        return {"ok": False, "src": str(src), "error": "нет исходника"}
    if not parse_tc(tc)[0] and not any(parse_tc(tc)):
        return {"ok": False, "src": str(src), "error": f"кривой таймкод: {tc}"}

    dst.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src),
         "-c", "copy", "-timecode", tc, str(dst)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode or not dst.is_file():
        return {"ok": False, "src": str(src),
                "error": (p.stderr or "").strip()[-300:] or "ffmpeg не создал файл"}

    src_sec = max((src.stat().st_size / 1e6), 0.001)
    return {"ok": True, "src": str(src), "dst": str(dst), "name": src.stem,
            "timecode": tc, "frame": tc_frames(tc, fps),
            "mb": round(dst.stat().st_size / 1e6, 1),
            "speed": round(src_sec, 1)}


def prepare(videos: List, outdir, tc: str = DEFAULT_TC,
            fps: int = 25, suffixes=(".mp4", ".mov", ".mxf",
                                      ".mkv", ".avi")) -> Dict:
    """Готовит папку для импорта в Premiere. Возвращает сводку."""
    outdir = Path(outdir)
    done, failed = [], []
    for v in videos:
        v = Path(v)
        if v.suffix.lower() not in suffixes:
            continue
        res = bake_timecode(v, outdir / v.name, tc, fps)
        (done if res.get("ok") else failed).append(res)
    total_mb = round(sum(r.get("mb", 0) for r in done), 1)
    return {"ok": bool(done) and not failed, "outdir": str(outdir),
            "timecode": tc, "done": done, "failed": failed,
            "clips": len(done), "mb": total_mb}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="AutoCut — подготовка камер к мультикаму Premiere")
    ap.add_argument("--input", nargs="+", required=True,
                    help="файлы камер (обычно из synced/)")
    ap.add_argument("--outdir", required=True, help="куда сложить копии")
    ap.add_argument("--timecode", default=DEFAULT_TC)
    ap.add_argument("--fps", type=int, default=25)
    a = ap.parse_args(argv)

    res = prepare(a.input, a.outdir, a.timecode, a.fps)
    if not res["ok"] and not res["clips"]:
        print("Ничего не подготовил:", res["failed"][0].get("error", "?"))
        return 1
    print(f"Готово: {res['clips']} камер, {res['mb']} МБ -> {res['outdir']}")
    for r in res["done"]:
        print(f"  {r['name']:<12} {r['timecode']}  {r['mb']} МБ")
    for r in res["failed"]:
        print(f"  ОШИБКА {Path(r['src']).name}: {r['error']}")

    print("\nДальше в Premiere:")
    print(f"  1. Импортируй всё из {res['outdir']}")
    print("  2. Выдели все файлы в панели Проект")
    print("  3. Правая кнопка > Multi-Camera > Create Multicam Sequence")
    print("  4. Synchronize Point: Timecode")
    print("  5. Camera Names: Use Clip Names")
    return 0 if not res["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
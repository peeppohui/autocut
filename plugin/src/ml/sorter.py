#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Разбор папки с материалами дайджеста: что стендап, что интервью, что нарезка.

Разбирать руками не надо: программа раскладывает файлы по трём папкам сама
и печатает, ПОЧЕМУ каждый файл попал туда, куда попал. Спорные случаи
помечаются отдельно, чтобы человек их посмотрел, а не удивлялся потом.

Замеры, на которых стоят пороги, сняты на настоящем материале медиацентра
(126 файлов: 110 нарезка, 13 стендап, 3 интервью):

| | нарезка | стендап | интервью |
|---|---|---|---|
| длительность | 4.8-27.4 с | 16.3-73.4 с | 100.8-359.0 с |

Длительность разошлась полностью, и это единственный признак, который
различает стендап и интервью без зазора. Доля речи не годится: нарезка
«говорит» на 44-57% шумом зала, а интервью на 61%, и медианы стоят
вплотную. Лицо в кадре тоже не годится: в зрительских планах люди есть
почти в каждом кадре, как и в стендапе.

Поэтому вторая ступень — ДЛИННЕЙШИЙ НЕПРЕРЫВНЫЙ КУСОК РЕЧИ. Паузы внутри
фразы (вдох) не рвут кусок, а пауза между фразами рвёт. На замере у
нарезки такой кусок 2.8-8.7 с, у стендапа 7.8-26.6 с. Помогает там, где
длительности сходятся, но в зоне 16-30 с признаки накладываются, и там
решение принимает порог, а не понимание. Такие файлы попадают в
`неоднозначно` и печатаются отдельно.

Ничего не двигает без `--apply`. Без него программа только показывает,
что получится.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # plugin/src/ml
SRC = HERE.parent
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "config"))

import bootstrap  # noqa: E402,F401
import settings   # noqa: E402

# Пороги стоят в настройках, а не в коде: это вкус съёмки, а не закон.
# Человек меняет число, а не код.
DEFAULTS = {
    "interview_min_sec": 90.0,   # длиннее - интервью
    "standup_min_sec": 14.0,     # короче этого - точно нарезка
    "speech_run_min_sec": 7.0,   # столько непрерывной речи нужно на стендап
    "gap_tol_sec": 0.5,          # пауза короче этого - часть одной фразы
}

PARTS = ("стендап", "нарезка", "интервью")
VIDEO_EXT = (".mp4", ".mov", ".mxf", ".avi", ".mts")


def part_names():
    """Названия папок берём из настроек, чтобы их можно было переименовать."""
    names = settings.section("digest").get("parts", {})
    out = {}
    for i, key in enumerate(("standup", "broll", "interview")):
        v = names.get(key) if isinstance(names, dict) else None
        out[key] = v or PARTS[i]
    return out


def find_input(root: Path):
    """Папка с самими файлами: корень или уже разложенная папка.

    Возвращает (папка, уже_разложено). Если внутри уже есть папки частей,
    файлы берём из них и сортировать ничего не надо.
    """
    names = part_names()
    subs = [root / names[k] for k in ("standup", "broll", "interview")]
    present = [p for p in subs if p.is_dir()]
    if len(present) >= 2:
        return root, True
    return root, False


def has_material(folder: Path) -> bool:
    """Есть ли в папке то, что сортировать.

    Проверяются не только файлы видео прямо в папке, но и три папки
    частей: материал может уже лежать разложенным (`исх/стендап/...`),
    и тогда видео в самой `исх` нет, но сортировать там есть что.
    """
    if collect(folder):
        return True
    names = part_names()
    subs = [p for p in folder.iterdir() if p.is_dir()] if folder.is_dir() else []
    return sum(1 for k in ("standup", "broll", "interview")
               if (folder / names[k]) in subs) >= 2


def find_materials(root: Path):
    """Где внутри корня лежит материал.

    Человек в окне указывает папку проекта, а не папку с ровно видео
    внутри: рядом лежат `звк`, результат прошлого прогона, `.prproj`.
    Раньше сортировщик смотрел только на корень и честно падал с «в папке
    нет видео» — хотя видео были, в одной вложенной папке.

    Ищем папку с материалом: сперва среди известных имён (`исх`,
    `исходники`, `видео`, `video`, `clips`), затем — любую единственную
    вложенную. Если кандидатов несколько и ни один не известный, это
    неоднозначность: говорим, какие варианты, и не гадаем.
    """
    if has_material(root):
        return root, None
    preferred = ("исх", "исходники", "видео", "video", "clips", "footage")
    subs = [p for p in root.iterdir() if p.is_dir()] if root.is_dir() else []
    for name in preferred:
        for p in subs:
            if p.name.lower() == name and has_material(p):
                return p, "материал в подпапке"
    with_video = [p for p in subs if has_material(p)]
    if len(with_video) == 1:
        return with_video[0], "материал в подпапке «{}»".format(with_video[0].name)
    if not with_video:
        return None, None
    return None, ("материал сразу в нескольких папках — укажите нужную: "
                  + ", ".join(p.name for p in with_video[:8]))


def duration_sec(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, errors="replace")
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def speech_run_sec(path: Path, gap_tol_sec: float = 0.5,
                   max_sec: float = 420.0) -> float:
    """Длиннейший непрерывный кусок речи, в секундах.

    Пауза короче gap_tol считается паузой внутри фразы и кусок не рвёт.
    Без этого вдох посреди предложения разрывает кусок надвое, и мера
    превращается в шум.

    Считается по огибающей громкости, без модели: этого хватает, чтобы
    отличить «человек говорит подряд» от «в зале что-то шумит».
    """
    import numpy as np
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-t", str(max_sec), "-i", str(path),
         "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        capture_output=True)
    a = np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0
    if a.size < 3200:
        return 0.0
    win = 1600                                   # 0.1 с
    n = a.size // win
    rms = np.array([np.sqrt((a[i * win:(i + 1) * win] ** 2).mean())
                    for i in range(n)])
    db = 20 * np.log10(np.maximum(rms, 1e-6))
    floor = float(np.percentile(db, 10))
    peak = float(db.max())
    if peak - floor < 6.0:                       # тишина или ровный шум
        return 0.0
    voiced = db > floor + (peak - floor) * 0.30
    gap = int(round(gap_tol_sec / 0.1))
    best = cur = missed = 0
    for v in voiced:
        if v:
            cur += 1
            missed = 0
        else:
            missed += 1
            if missed <= gap:
                cur += 1
            else:
                best = max(best, cur)
                cur = 0
    return max(best, cur) * 0.1


def classify(path: Path, th: dict) -> dict:
    """Куда относится файл и почему."""
    dur = duration_sec(path)
    if dur <= 0:
        return dict(part="нарезка", dur=0.0, run=0.0, sure=True,
                    why="не читается, отношу к нарезке")
    if dur >= th["interview_min_sec"]:
        return dict(part="интервью", dur=dur, run=0.0, sure=True,
                    why="длительность {:.0f}с от {:.0f}с - это интервью"
                        .format(dur, th["interview_min_sec"]))
    if dur < th["standup_min_sec"]:
        return dict(part="нарезка", dur=dur, run=0.0, sure=True,
                    why="короткий кусок {:.1f}с - это нарезка".format(dur))
    run = speech_run_sec(path, th["gap_tol_sec"])
    if run >= th["speech_run_min_sec"]:
        return dict(part="стендап", dur=dur, run=run, sure=True,
                    why="говорит подряд {:.1f}с - стендап".format(run))
    return dict(part="нарезка", dur=dur, run=run, sure=False,
                why="длительность {:.0f}с как у стендапа, но непрерывной речи "
                    "всего {:.1f}с".format(dur, run))


def collect(root: Path):
    files = sorted(p for p in root.iterdir()
                   if p.is_file() and p.suffix.lower() in VIDEO_EXT)
    return files


def check_already(root: Path, names: dict, th: dict, args) -> int:
    """Сверяет сортировку с тем, что лежит по папкам.

    Нужна не для красоты: когда материал разложили руками, непонятно,
    правильно ли. Здесь видно каждое расхождение и почему алгоритм решил
    иначе — и это же показывает, где пороги кривые.
    """
    truth = {}
    for key in ("standup", "broll", "interview"):
        p = root / names[key]
        if not p.is_dir():
            continue
        for f in sorted(p.iterdir()):
            if f.suffix.lower() in VIDEO_EXT:
                truth[f.name] = names[key]
    if not truth:
        print("В папках частей нет видео.")
        return 1

    print("Проверяю разложенный материал, файлов: {}\n".format(len(truth)))
    rows = []
    wrong = []
    for i, (fname, real) in enumerate(sorted(truth.items()), 1):
        r = classify(root / real / fname, th)
        rows.append(dict(file=fname, part=r["part"], dur=r["dur"],
                         run=r["run"], sure=r["sure"], why=r["why"],
                         real=real, ok=(r["part"] == real)))
        if r["part"] != real:
            wrong.append((fname, real, r))
        print("[{}/{}] {:<14} -> {:<14} {}  {}".format(
            i, len(truth), real, r["part"],
            "ок" if r["part"] == real else "РАСХОЖДЕНИЕ", r["why"]))

    out = Path(args.out) if args.out else root / "sorter_check.csv"
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["file", "part", "real", "ok",
                                          "dur", "run", "sure", "why"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    n_ok = len(rows) - len(wrong)
    print("\nСовпало: {} из {}  ({:.0f}%)".format(n_ok, len(rows),
                                                 n_ok / len(rows) * 100))
    if wrong:
        print("\nРасхождения:")
        for fname, real, r in wrong:
            print("  {:<14} лежит в «{}», алгоритм сказал «{}» — {}"
                  .format(fname, real, r["part"], r["why"]))
    print("\nОтчёт: " + str(out))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Разложить материалы дайджеста по трём частям")
    ap.add_argument("--input", required=True, help="папка с материалами")
    ap.add_argument("--out", help="куда писать отчёт (по умолчанию — "
                                  "рядом с исходниками, sorter.csv)")
    ap.add_argument("--check", action="store_true",
                    help="сверить уже разложенные папки с алгоритмом")
    ap.add_argument("--apply", action="store_true",
                    help="разложить файлы по папкам (без этого только отчёт)")
    ap.add_argument("--device", default="auto")
    args = ap.parse_args(argv)

    root = Path(args.input).expanduser()
    if not root.is_dir():
        print("Нет папки: " + str(root))
        return 1

    names = part_names()
    th = dict(DEFAULTS)
    cfg = settings.section("digest").get("parts", {})
    if isinstance(cfg, dict):
        for k in DEFAULTS:
            if isinstance(cfg.get(k), (int, float)):
                th[k] = float(cfg[k])

    # Материал может лежать не прямо в корне, а во вложенной папке.
    src_root, hint = find_materials(root)
    if src_root is None:
        if hint:
            print(hint)
            return 1
        print("В папке нет видео: " + str(root))
        return 1
    if hint:
        print("{}: {}".format(hint, src_root))
    root = src_root

    already, sorted_already = find_input(root)
    if sorted_already and not args.check:
        print("Материал уже разложен по частям. Перекладывать не нужно.")
        for key in ("standup", "broll", "interview"):
            p = already / names[key]
            if p.is_dir():
                n = len([f for f in p.iterdir()
                         if f.suffix.lower() in VIDEO_EXT])
                print("  {:<10} файлов: {}".format(names[key], n))
        print("\nПроверить, что разложено верно: добавьте --check")
        return 0

    if sorted_already:
        return check_already(root, names, th, args)

    files = collect(root)
    if not files:
        print("В папке нет видео: " + str(root))
        return 1
    print("Файлов: {}\n".format(len(files)))

    rows = []
    for i, p in enumerate(files, 1):
        r = classify(p, th)
        r["file"] = p.name
        rows.append(r)
        print("[{}/{}] {:<14} {:>7.1f}с  речь {:>5.1f}с  {}".format(
            i, len(files), r["part"], r["dur"], r["run"], r["why"]))

    out = Path(args.out) if args.out else root / "sorter.csv"
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["file", "part", "dur", "run",
                                          "sure", "why"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("\nОтчёт: " + str(out))

    unsure = [r for r in rows if not r["sure"]]
    for key in ("standup", "broll", "interview"):
        n = len([r for r in rows if r["part"] == names[key]])
        print("  {:<10} файлов: {}".format(names[key], n))
    if unsure:
        print("\nНеоднозначно (посмотрите глазами):")
        for r in unsure:
            print("  {:<14} {}".format(r["file"], r["why"]))

    if not args.apply:
        print("\nЭто только отчёт. Чтобы разложить по папкам, добавьте --apply.")
        return 0

    for r in rows:
        dst_dir = root / names[
            {"стендап": "standup", "нарезка": "broll",
             "интервью": "interview"}[r["part"]]]
        dst_dir.mkdir(exist_ok=True)
        src = root / r["file"]
        dst = dst_dir / r["file"]
        if dst.exists():
            print("Уже есть, пропускаю: " + str(dst))
            continue
        try:
            src.rename(dst)
        except OSError as e:
            print("Не смог переложить {}: {}".format(src.name, e))
    print("\nРазложено.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
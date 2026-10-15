"""Лестница файлов для проверки импорта в Premiere.

Зачем. Premiere на отказ импортировать не отвечает НИЧЕМ: пустая панель
видео, ни слова в консоли. Поэтому файл проверяют по возрастанию
сложности, и первым не открывшийся становится виновник.

Здесь собирается четыре файла из ОДНОГО и того же настоящего
материала, так что единственное различие между ними - устройство
секвенции.

Запуск:
    python make_premiere_ladder.py
"""
import csv
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SRC = Path(r"E:\AutoCut\plugin\src")
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "io"))
sys.path.insert(0, str(SRC / "ml"))
import bootstrap  # noqa

import premiere  # noqa: E402

PROJECT = Path(r"E:\интервью расскажи\проект")
RZ = PROJECT / "rz"
OUT = PROJECT / "ladder"


def export(name, rows, stacked=False, width=None, height=None):
    """Собирает XML через настоящий premiere.py, а не руками."""
    OUT.mkdir(parents=True, exist_ok=True)
    csv_in = OUT / f"{name}.csv"
    fields = ["file", "plan", "hero", "duration", "in", "out", "desc",
              "path", "audio", "alt", "alt_path", "track"]
    with open(csv_in, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    xml = OUT / f"{name}.xml"
    argv = ["--timeline", str(csv_in), "--media-dir", str(RZ),
            "--out", str(xml), "--edl", str(OUT / f"{name}.edl")]
    if stacked:
        argv.append("--stacked")
    if width:
        argv += ["--width", str(width), "--height", str(height)]
    rc = premiere.main(argv)
    root = ET.parse(xml).getroot()
    errs = premiere.check_xml(root)
    n_v = len(root.findall(".//video/track/clipitem"))
    n_a = len(root.findall(".//audio/track/clipitem"))
    ok = "чисто" if not errs else "; ".join(errs)[:120]
    print(f"  {name}.xml  код {rc}  видео {n_v}  звук {n_a}  проверка: {ok}")
    if premiere.missing_media(root):
        print(f"    ! нет файлов на диске: {premiere.missing_media(root)[:2]}")
    return rc


def main():
    src_csv = RZ / "selects.csv"
    if not src_csv.is_file():
        print(f"Нет рыбы: {src_csv}\nСначала собери интервью.")
        return 2
    with open(src_csv, encoding="utf-8-sig", newline="") as f:
        rows = [dict(r) for r in csv.DictReader(f)]

    # ступень на две реплики: предисловия и альтов не нужно, чтобы
    # было видно, работает ли вообще базовый импорт
    two = []
    for r in rows:
        if (r.get("track") or "V1").upper() == "V1" and r.get("path"):
            two.append(r)
        if len(two) == 2:
            break

    print("Строю лестницу. Импортируй по порядку: первый не открывшийся "
          "файл и есть виновник.")
    print(f"\nПапка: {OUT}\n")

    export("1_plain", two)
    export("2_stacked", two, stacked=True)
    export("3_full", rows, stacked=True)

    vert = RZ.parent / "vertical" / "selects.csv"
    if vert.is_file():
        with open(vert, encoding="utf-8-sig", newline="") as f:
            vrows = [dict(r) for r in csv.DictReader(f)]
        export("4_vertical", vrows, stacked=True, width=1080, height=1920)
    else:
        print("  4_vertical.xml пропущен: вертикальной рыбы ещё нет "
              "(собери блоком «Вертикаль 9:16»).")

    print("\nЕсли 1 не открылся - дело не в нашей разметке, а в самом "
          "FCP7 на этой машине.")
    print("Если 1 открылся, а 2 нет - виноват второй ракурс.")
    print("Если 2 открылся, а 3 нет - виновато наполнение дорожек.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
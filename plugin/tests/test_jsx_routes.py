# -*- coding: utf-8 -*-
"""Проверка раскладки дорожек: повторяет логику assemble.jsx на Python.

Скрипт на ExtendScript нельзя запустить без Premiere, поэтому логика
routeOf/ensureTrack дублируется здесь и проверяется на настоящих CSV.
Дублирование намеренное: держим обе копии под тестами.
"""
import csv
import re
import sys
from pathlib import Path


def route_of(tr):
    """Тот же разбор, что routeOf() в assemble.jsx."""
    if isinstance(tr, dict):
        tr = tr.get("track", "")
    tr = (tr or "").upper().replace(" ", "")
    m = re.fullmatch(r"V(\d+)", tr)
    if m:
        vi = int(m.group(1))
        if 1 <= vi <= 4:
            return {"video": vi - 1, "audio": vi - 1}
    m = re.fullmatch(r"A(\d+)", tr)
    if m:
        ai = int(m.group(1))
        if 1 <= ai <= 4:
            return {"video": -1, "audio": ai - 1}
    return {"video": 0, "audio": 0}


def need_tracks(rows):
    """Сколько дорожек потребуется, чтобы всё влезло."""
    nv = na = 0
    for r in rows:
        rt = route_of(r)
        if rt["video"] >= 0:
            nv = max(nv, rt["video"] + 1)
        if rt["audio"] >= 0:
            na = max(na, rt["audio"] + 1)
    return max(nv, 1), max(na, 1)


def read_rows(path):
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            if row.get("file"):
                out.append(row)
    return out


fail = []


def eq(name, got, want):
    if got == want:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}\n     получено {got!r}\n     ждалось   {want!r}")


# --- разбор дорожек ---
eq("пусто -> V1/A1", route_of(""), {"video": 0, "audio": 0})
eq("пробелы убраны", route_of("  v2 "), {"video": 1, "audio": 1})
eq("V1 -> первая пара", route_of("V1"), {"video": 0, "audio": 0})
eq("V3 -> третья пара", route_of("V3"), {"video": 2, "audio": 2})
eq("A2 -> только звук", route_of("A2"), {"video": -1, "audio": 1})
eq("A1 -> звук без видео", route_of("A1"), {"video": -1, "audio": 0})
eq("мусор -> V1/A1", route_of("чем-то"), {"video": 0, "audio": 0})
eq("V9 вне диапазона", route_of("V9"), {"video": 0, "audio": 0})
eq("пустая ячейка", route_of(None), {"video": 0, "audio": 0})

# --- сколько дорожек нужно ---
eq("одна дорожка", need_tracks([{"track": ""}]), (1, 1))
eq("до V3 и A4", need_tracks([{"track": "V3"}, {"track": "A4"}]), (3, 4))
eq("только звук A2", need_tracks([{"track": "A2"}]), (1, 2))
eq("пустой таймлайн", need_tracks([]), (1, 1))

# --- настоящий CSV интервью ---
csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if csv_path and csv_path.is_file():
    rows = read_rows(csv_path)
    nv, na = need_tracks(rows)
    print(f"\nтаймлайн {csv_path.name}: {len(rows)} строк, нужно V{nv} A{na}")
    has_audio = sum(1 for r in rows if r.get("audio"))
    print(f"строк с отдельным микрофоном: {has_audio}")
    eq("дорожки по умолчанию", (nv, na), (1, 1))
    eq("микрофоны присутствуют", has_audio > 0, True)
    eq("колонка track пустая (обратная совместимость)",
       all(not r.get("track") for r in rows), True)
else:
    print("\nCSV не передан — проверка на настоящем файле пропущена")

print()
if fail:
    print(f"ROUTE FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("ROUTE OK")
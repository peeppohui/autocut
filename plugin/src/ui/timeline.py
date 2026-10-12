# -*- coding: utf-8 -*-
"""Раскладка монтажа по дорожкам: чистая логика, без Tkinter.

Модуль намеренно без интерфейса. Всё, что здесь, можно проверить
тестом без окна, и именно это проверяет tests/test_ui.py. Отрисовка
живёт отдельно, в ui/panel_result.py.

Что тут считается. Premiere складывает клипы подряд, без зазоров, и
позиция куска на таймлайне - это сумма длительностей всех предыдущих.
Тот же подсчёт делает io/premiere.py при сборке XML; если разойтись,
рисунок в окне и файл у Premiere будут показывать разное.
"""
from __future__ import annotations

import csv
from pathlib import Path

VIDEO_EXT = (".mp4", ".mov", ".mxf", ".mkv", ".avi", ".m4v")
REST = "__extra__"          # ключ, под который csv кладёт лишние колонки


def mmss(sec: float) -> str:
    s = int(round(max(0.0, sec)))
    return "%d:%02d" % (s // 60, s % 60)


def _cell(v):
    """Значение ячейки как строка.

    Если в строке колонок больше, чем в заголовке, DictReader кладёт
    лишние в список под ключом None. Раньше такая раскладка роняла
    страницу с «list object has no attribute strip» - а такой CSV
    легко получается, если его правили в Excel.
    """
    if isinstance(v, (list, tuple)):
        return ""
    return str(v or "").strip()


def read_rows(csv_path) -> tuple:
    """Строки раскладки со всеми колонками и типизированными числами.

    Не берём preview.load_selects: он выкидывает куски без in/out, а
    в монтаже они есть и занимают место на дорожке.
    """
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f, restkey=REST, restval="")
        fieldnames = [k for k in (rdr.fieldnames or []) if k and k != REST]
        rows = []
        for row in rdr:
            clean = {}
            for k, v in row.items():
                if k is None or k == REST:
                    continue      # лишние колонки нам не нужны
                key = str(k).strip().lower()
                if key:
                    clean[key] = _cell(v)
            rows.append(clean)
    for r in rows:
        for key in ("duration", "in", "out"):
            try:
                r[key] = float(str(r.get(key) or "").replace(",", "."))
            except ValueError:
                r[key] = 0.0
    return rows, fieldnames


def find_csv(project_dir) -> Path | None:
    """Ищет раскладку в папке проекта.

    Интервью и сюжет кладут selects.csv в rz/, дайджест - timeline.csv
    прямо в папку. Перебираем оба варианта.
    """
    p = Path(project_dir)
    for rel in ("rz/selects.csv", "selects.csv",
                "rz/timeline.csv", "timeline.csv"):
        cand = p / rel
        if cand.is_file():
            return cand
    return None


def _lane(store: dict, order: list, name: str, kind: str) -> dict:
    if name not in store:
        store[name] = {"name": name, "kind": kind, "items": []}
        order.append(name)
    return store[name]


def build_timeline(rows) -> dict:
    """Раскладывает куски по дорожкам и считает общий хронометраж.

    Видео со вторым ракурсом рисуется на двух дорожках: верхний кадр и
    нижний, переключают глазком. Так же он ложится в XML при --stacked.
    """
    store, order = {}, []
    t = 0.0
    for idx, r in enumerate(rows):
        dur = float(r.get("duration") or 0.0)
        if dur <= 0:
            dur = 0.001      # иначе кусок не виден и не нажмётся
        raw = (r.get("track") or "").strip().upper()
        if raw.startswith("A"):
            # самостоятельная звуковая дорожка: закадр, подложка.
            # Видео у неё нет, и на V1 попадать ей нельзя.
            v = ""
        elif raw.startswith("V"):
            v = raw
        else:
            v = "V1"
        if v:
            _lane(store, order, v, "video")["items"].append(
                {"row": idx, "start": t, "dur": dur, "role": "main"})
            if (r.get("alt") or "").strip():
                _lane(store, order, "V2", "alt")["items"].append(
                    {"row": idx, "start": t, "dur": dur, "role": "alt"})

        a = (r.get("audio") or "").strip()
        if raw.startswith("A"):
            label = "♪ " + Path(r.get("file") or "звук").name
            _lane(store, order, label, "audio")["items"].append(
                {"row": idx, "start": t, "dur": dur, "role": "own"})
        elif a:
            label = "♪ " + Path(a).name
            _lane(store, order, label, "audio")["items"].append(
                {"row": idx, "start": t, "dur": dur, "role": "linked"})
        t += dur

    lanes = [store[n] for n in order]
    # видео сверху, звук снизу - так же, как в Premiere
    lanes.sort(key=lambda l: (0 if l["kind"] in ("video", "alt") else 1))
    return {"lanes": lanes, "total": t, "count": len(rows)}


def swap_angle(row: dict) -> bool:
    """Меняет ракурсы местами: верхний нижним и наоборот.

    Один кусок занимает одну строку CSV, а второй ракурс лежит в
    колонках alt/alt_path той же строки. Значит «переставить ракурсы»
    - это обмен четырьмя значениями, а не перестановка строк.

    False, если второго ракурса нет: менять нечего, и пустая кнопка
    в интерфейсе врёт.
    """
    if not (row.get("alt") or "").strip() or not (row.get("alt_path") or "").strip():
        return False
    row["file"], row["alt"] = row.get("alt", ""), row.get("file", "")
    row["path"], row["alt_path"] = row.get("alt_path", ""), row.get("path", "")
    return True


def has_alt(row: dict) -> bool:
    return bool((row.get("alt") or "").strip()
                and (row.get("alt_path") or "").strip())


def _fmt(v):
    """Числа в CSV пишем без хвостовых нулей: 10.0 -> 10."""
    if isinstance(v, float):
        return "%g" % v
    return v


def write_rows(csv_path, rows, fieldnames) -> None:
    """Записывает раскладку обратно. Колонки берём из исходного файла.

    Рядом остаётся .bak: правка идёт поверх чужого труда, откатывать
    придётся вручную.
    """
    csv_path = Path(csv_path)
    backup = csv_path.with_suffix(csv_path.suffix + ".bak")
    if csv_path.is_file():
        backup.write_bytes(csv_path.read_bytes())
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _fmt(r.get(k, "")) for k in fieldnames})

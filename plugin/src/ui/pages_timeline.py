# -*- coding: utf-8 -*-
"""Таймлайн монтажа: дорожки как в Premiere и правка раскладки.

Смотр и правка разделены на две части, и это не случайно. Первая -
чистые функции без Tkinter: их дёргает тест и они же считают, где
какой кусок лежит на шкале. Вторая - рисование.

Почему дорожки вообще нужны. Автомат собирает рыбу, но монтажёр всё
равно идёт в Premiere и там видит то же самое глазами. Разница в
одном: Premiere показывает ДОРОЖКИ. Ошибка «реплика уехала на
соседний ракурс» на плоском списке не видна, на дорожках видна сразу.

Почему правка здесь, а не в Premiere. Самая частая ручная операция
после автосборки - выкинуть кусок или переставить ракурс. Раньше это
означало открыть selects.csv в блокноте, что для монтажёра неприемлемо.
Прежний CSV не трогаем до кнопки «Сохранить», и рядом кладём копию.

Про tkinter.Canvas, а не ctk: у customtkinter просто нет виджета
полотна. Цвета берём из theme.py, поэтому фон и рамки совпадают с
остальным окном.
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import tkinter as tk

import customtkinter as ctk

HERE = Path(__file__).resolve().parent        # plugin/src/ui
SRC = HERE.parent                            # plugin/src
BASE = SRC.parents[1]                        # корень проекта
IO = SRC / "io"

sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "config"))

import theme as T                            # noqa: E402

LANE_H = 38            # высота дорожки
RULER_H = 24           # полоса времени
LANE_GAP = 4
MIN_PPS = 2.0          # пикселей на секунду, меньше - копейки не прочитать
MAX_PPS = 80.0
DEFAULT_PPS = 12.0
CLIP_MIN_W = 3         # уже не кликнуть, но всё равно рисуем


# ==================================================================
#  чистая часть: раскладка по дорожкам. Без Tkinter.
# ==================================================================
def read_rows(csv_path) -> list:
    """Строки раскладки со всеми колонками и типизированными числами.

    Не берём preview.load_selects: он выкидывает куски без in/out, а
    в монтаже они есть и занимают место на дорожке.
    """
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f)
        fieldnames = list(rdr.fieldnames or [])
        rows = [{(k or "").strip().lower(): (v or "").strip()
                 for k, v in row.items()} for row in rdr]
    for r in rows:
        for key in ("duration", "in", "out"):
            try:
                r[key] = float(str(r.get(key) or "").replace(",", "."))
            except ValueError:
                r[key] = 0.0
    return rows, fieldnames


def mmss(sec: float) -> str:
    s = int(round(max(0.0, sec)))
    return "%d:%02d" % (s // 60, s % 60)


def _lane(store: dict, order: list, name: str, kind: str) -> dict:
    if name not in store:
        store[name] = {"name": name, "kind": kind, "items": []}
        order.append(name)
    return store[name]


def build_timeline(rows) -> dict:
    """Раскладывает куски по дорожкам и считает общий хронометраж.

    Premiere складывает клипы подряд, без зазоров, поэтому позиция
    куска на таймлайне - это сумма длительностей всех предыдущих. Тот
    же подсчёт делает premiere.py при сборке, иначе рисунок и файл
    разошлись бы.

    Видео с вторым ракурсом рисуется на двух дорожках: верхний кадр и
    нижний, между ними переключают глазком. Так же он ложится в XML.
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

        at = (r.get("track") or "").upper()
        a = (r.get("audio") or "").strip()
        if at.startswith("A"):
            # самостоятельная звуковая дорожка: закадр, подложка
            label = "♪ " + Path(r.get("file") or "звук").name
            _lane(store, order, label, "audio")["items"].append(
                {"row": idx, "start": t, "dur": dur, "role": "own"})
        elif a:
            # связанный звук: микрофон говорящего
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


def write_rows(csv_path, rows, fieldnames) -> None:
    """Записывает раскладку обратно. Колонки берём из исходного файла."""
    csv_path = Path(csv_path)
    backup = csv_path.with_suffix(csv_path.suffix + ".bak")
    if csv_path.is_file():
        # копия обязательна: правка идёт поверх чужого труда, откатить
        # придётся вручную
        backup.write_bytes(csv_path.read_bytes())
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _fmt(r.get(k, "")) for k in fieldnames})


def _fmt(v):
    """Числа в CSV пишем без хвостовых нулей: 10.0 -> 10."""
    if isinstance(v, float):
        return ("%g" % v)
    return v


# ==================================================================
#  отрисовка
# ==================================================================
def page_timeline(parent, app):
    wrap = ctk.CTkFrame(parent, fg_color="transparent")

    src = T.card(wrap, "Что смотрим", "Правки держатся в памяти, пока не "
                 "нажата кнопка сохранения")
    sel = ctk.StringVar()
    T.field(src, 0, "selects.csv", sel, "папка\\rz\\selects.csv",
            browse=lambda: app.pick_file(sel, "selects.csv",
                                         [("Раскладка", "*.csv")]))

    bar = ctk.CTkFrame(src, fg_color="transparent")
    bar.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))
    zoom_lbl = ctk.StringVar(value="")
    T.btn(bar, "−", "ghost", width=38, command=lambda: zoom(-1)).pack(
        side="left")
    ctk.CTkLabel(bar, textvariable=zoom_lbl, text_color=T.MUTED, width=110,
                 anchor="w", font=T.font(12)).pack(side="left", padx=6)
    T.btn(bar, "+", "ghost", width=38, command=lambda: zoom(1)).pack(
        side="left")
    T.btn(bar, "Показать", "primary", width=120,
          command=lambda: load()).pack(side="left", padx=(14, 0))
    stacked = ctk.BooleanVar(value=True)
    ctk.CTkSwitch(bar, text="второй ракурс на V2", variable=stacked,
                  progress_color=T.ACCENT, text_color=T.MUTED,
                  font=T.font(12)).pack(side="right")

    # ---- полотно ----
    holder = ctk.CTkFrame(wrap, fg_color="transparent")
    holder.pack(fill="both", expand=True, padx=T.PAD)
    scroll = ctk.CTkScrollableFrame(holder, fg_color="transparent")
    scroll.pack(fill="both", expand=True)
    canvas = tk.Canvas(scroll, bg=T.SURFACE, highlightthickness=1,
                       highlightbackground=T.BORDER, bd=0)
    canvas.pack(anchor="w")
    hbar = ctk.CTkScrollbar(holder, orientation="horizontal",
                            command=canvas.xview)
    hbar.pack(fill="x", pady=(6, 0))

    # ---- нижняя панель: выбранный кусок и действия ----
    info = ctk.StringVar(value="Ничего не выбрано")
    detail = T.card(wrap, "Выбранный кусок")
    pic_lbl = ctk.CTkLabel(detail, text="", width=192, height=108,
                           corner_radius=6, fg_color=T.RAISED)
    pic_lbl.grid(row=0, column=0, rowspan=4, sticky="nw", padx=(0, 14))
    ctk.CTkLabel(detail, textvariable=info, text_color=T.TEXT, anchor="w",
                 justify="left", wraplength=520,
                 font=T.font(12)).grid(row=0, column=1, columnspan=2,
                                       sticky="w")

    acts = ctk.CTkFrame(wrap, fg_color="transparent")
    acts.pack(fill="x", padx=T.PAD, pady=(0, 10))
    # кнопки создаются раньше функций, поэтому через лямбду: иначе
    # UnboundLocalError на этапе сборки страницы, а не при нажатии
    b_drop = T.btn(acts, "Выкинуть кусок", "danger", width=170,
                   command=lambda: drop(), state="disabled")
    b_drop.pack(side="left")
    b_swap = T.btn(acts, "Поменять ракурсы", "ghost", width=190,
                   command=lambda: swap(), state="disabled")
    b_swap.pack(side="left", padx=10)
    b_back = T.btn(acts, "Вернуть как было", "ghost", width=190,
                   command=lambda: reload_disk(), state="disabled")
    b_back.pack(side="left")

    # ---- состояние ----
    st = {"rows": [], "fields": [], "csv": None, "pps": DEFAULT_PPS,
          "sel": None, "dirty": False, "boxes": []}

    def csv_path():
        p = Path(sel.get().strip().strip('"'))
        if p.is_dir():
            p = p / "rz" / "selects.csv"
        return p

    # ---------------------------------------------------------- загрузка
    def load():
        p = csv_path()
        if not p.is_file():
            info.set(f"Файл не найден: {p}")
            return
        try:
            rows, fields = read_rows(p)
        except Exception as e:
            info.set(f"Не прочитал раскладку: {e}")
            return
        st["rows"], st["fields"], st["csv"] = rows, fields, p
        st["dirty"] = False
        st["sel"] = None
        set_sel(None)
        draw()

    def reload_disk():
        load()

    # ---------------------------------------------------------- рисование
    def zoom(step):
        st["pps"] = max(MIN_PPS, min(MAX_PPS, st["pps"] * (1.25 if step > 0
                                                          else 0.8)))
        draw()

    def draw():
        canvas.delete("all")
        st["boxes"] = []
        tl = build_timeline(st["rows"])
        pps = st["pps"]
        zoom_lbl.set(f"{pps:.0f} пикс/сек")
        if not tl["lanes"]:
            canvas.configure(width=600, height=RULER_H + 20)
            canvas.create_text(12, 14, anchor="w", text="Пусто",
                               fill=T.MUTED, font=("Segoe UI", 12))
            return

        w = max(400, int(tl["total"] * pps) + 40)
        h = RULER_H + len(tl["lanes"]) * (LANE_H + LANE_GAP)
        canvas.configure(width=w, height=h + 8)
        canvas.configure(scrollregion=(0, 0, w, h + 8))
        hbar.configure(command=canvas.xview)
        canvas.configure(xscrollcommand=hbar.set)

        # линейка: деления, подписи кратны 5 секундам, чтобы не слиплись
        step = 5 if pps >= 8 else (10 if pps >= 3 else 30)
        t = 0.0
        while t <= tl["total"]:
            x = t * pps
            canvas.create_line(x, 0, x, h, fill=T.BORDER)
            canvas.create_text(x + 3, 3, anchor="nw", text=mmss(t),
                               fill=T.FAINT, font=("Segoe UI", 9))
            t += step
        canvas.create_text(w - 4, 3, anchor="ne",
                           text=f"всего {mmss(tl['total'])} · "
                                f"кусков {tl['count']}",
                           fill=T.MUTED, font=("Segoe UI", 10))

        for li, lane in enumerate(tl["lanes"]):
            y = RULER_H + li * (LANE_H + LANE_GAP)
            fill = T.RAISED if lane["kind"] == "alt" else T.BG
            canvas.create_rectangle(0, y, w, y + LANE_H, fill=fill,
                                    outline="")
            canvas.create_text(6, y + LANE_H // 2, anchor="w",
                              text=lane["name"], fill=T.FAINT,
                              font=("Segoe UI", 9))
            for it in lane["items"]:
                x0 = it["start"] * pps
                x1 = x0 + max(CLIP_MIN_W, it["dur"] * pps)
                row = st["rows"][it["row"]]
                c = _clip_color(lane["kind"], it["role"])
                box = canvas.create_rectangle(
                    x0 + 2, y + 3, x1 - 2, y + LANE_H - 3,
                    fill=c, outline=T.BORDER, width=1,
                    tags=("clip", f"r{it['row']}"))
                st["boxes"].append(box)
                if x1 - x0 > 46:
                    label = (row.get("hero") or row.get("desc")
                             or row.get("file") or "")
                    canvas.create_text(
                        x0 + 7, y + LANE_H // 2, anchor="w",
                        text=label[:max(1, int((x1 - x0) / 7))],
                        fill="#ffffff", font=("Segoe UI", 9),
                        tags=("clip", f"r{it['row']}"))
                # подпись рисуется после прямоугольника и оказывается
                # поверх него: клик должен находить строку по тегу на
                # любом из двух элементов, иначе попадание в буквы
                # не считалось бы нажатием на кусок

    canvas.tag_raise("clip")

    def _clip_color(kind, role):
        if kind == "alt":
            return T.ACCENT_DIM
        if kind == "audio":
            return "#2a4a3a" if role == "linked" else "#3a3550"
        return T.ACCENT if role == "main" else T.ACCENT_DIM

    # ---------------------------------------------------------- выбор
    def on_click(ev):
        i = canvas.find_closest(ev.x, ev.y)[0]
        for box in st["boxes"]:
            if box == i:
                tag = canvas.gettags(box)
                rid = next((t for t in tag if t.startswith("r")), None)
                if rid:
                    set_sel(int(rid[1:]))
                return
        set_sel(None)

    canvas.bind("<Button-1>", on_click)

    keep_img = []

    def set_sel(idx):
        st["sel"] = idx
        if idx is None:
            b_drop.configure(state="disabled")
            b_swap.configure(state="disabled")
            return
        b_drop.configure(state="normal")
        row = st["rows"][idx]
        can_swap = bool((row.get("alt") or "").strip()
                        and (row.get("alt_path") or "").strip())
        b_swap.configure(state="normal" if can_swap else "disabled")
        a, b = row.get("in", 0.0), row.get("out", 0.0)
        info.set(
            f"кусок {idx + 1} из {len(st['rows'])}\n"
            f"{(row.get('hero') or '-')} · {(row.get('plan') or '-')} · "
            f"{row.get('duration', 0):.1f} с\n"
            f"файл: {row.get('file') or '-'}\n"
            f"исходник: {mmss(a)} – {mmss(b)}"
            f"   ракурс: {row.get('alt') or 'нет'}"
            f"   звук: {Path(row.get('audio')).name if row.get('audio') else '-'}"
        )
        show_pic(row)

    def show_pic(row):
        keep_img.clear()
        src = (row.get("path") or "").strip()
        if not src or not Path(src).is_file():
            pic_lbl.configure(image=None, text="нет кадра")
            return
        try:
            import preview as pv
            td = st["csv"].parent / "thumbs"
            td.mkdir(parents=True, exist_ok=True)
            dst = td / pv.thumb_name(row)
            if not dst.is_file():
                pv.grab_frame(src, (row.get("in", 0) + row.get("out", 0)) / 2,
                              dst)
            if not dst.is_file():
                pic_lbl.configure(image=None, text="не вышло")
                return
            img = tk.PhotoImage(file=str(dst))
            if img.width() > 192:
                img = img.subsample(max(1, img.width() // 192))
            keep_img.append(img)
            pic_lbl.configure(image=img, text="")
        except Exception:
            pic_lbl.configure(image=None, text="нет кадра")

    # ---------------------------------------------------------- правка
    def mark_dirty():
        st["dirty"] = True
        b_back.configure(state="normal")

    def drop():
        if st["sel"] is None:
            return
        st["rows"].pop(st["sel"])
        st["sel"] = None
        mark_dirty()
        set_sel(None)
        draw()

    def swap():
        if st["sel"] is None:
            return
        if swap_angle(st["rows"][st["sel"]]):
            mark_dirty()
            set_sel(st["sel"])
            draw()

    # ---------------------------------------------------------- сборка
    def save_and_build():
        if st["csv"] is None:
            app.warn("Сначала открой раскладку кнопкой «Показать».")
            return
        if not st["dirty"]:
            app.warn("Правок нет — нечего сохранять.")
            return
        try:
            write_rows(st["csv"], st["rows"], st["fields"])
        except Exception as e:
            app.warn(f"Не сохранил раскладку: {e}")
            return
        st["dirty"] = False
        b_back.configure(state="disabled")
        project = st["csv"].parent.parent
        xml = project / "ryba.xml"
        argv = [str(IO / "premiere.py"), "--timeline", str(st["csv"]),
                "--media-dir", str(project), "--out", str(xml),
                "--edl", str(project / "ryba.edl"), "--trim-rec"]
        if stacked.get():
            argv.append("--stacked")
        steps = [("1. XML для Premiere", argv)]
        srt_out = project / "ryba.srt"
        if srt_out.is_file():
            # хронометраж после правки поехал, старые субтитры больше
            # не совпадают с картинкой
            steps.append(("2. Субтитры",
                          [str(IO / "srt.py"), "--selects", str(st["csv"]),
                           "--out", str(srt_out)]))
        app.log(f"Раскладка сохранена, копия: {st['csv']}.bak")
        app.go(steps, str(project))

    b_save = T.btn(acts, "Сохранить и пересобрать", "primary", width=240,
                   command=lambda: save_and_build())
    b_save.pack(side="right")
    T.btn(acts, "Открыть папку", "quiet", width=150,
          command=lambda: st["csv"] and os.startfile(str(st["csv"].parent))
          ).pack(side="right", padx=10)

    return wrap

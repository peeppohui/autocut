# -*- coding: utf-8 -*-
"""Страница «Новостной сюжет».

Логика раскладки живёт в modes/news_package.py и покрыта tests/test_news.py.
Здесь только форма: собрать из полей spec.json и запустить цепочку шагов,
как это делают остальные страницы.

Страница вынесена из app.py отдельно: там уже семь страниц, а сюжет с
повторяющимися строками спикеров тянет на полторы сотни строк. Раньше
все страницы лежали в app.py — при добавлении восьмой и девятой файл
перестал бы читаться.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import customtkinter as ctk

HERE = Path(__file__).resolve().parent        # plugin/src/ui
SRC = HERE.parent                            # plugin/src
BASE = SRC.parents[1]                        # корень проекта
MODES = SRC / "modes"
IO = SRC / "io"

sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "config"))

import theme as T                            # noqa: E402

VIDEO_EXT = (".mp4", ".mov", ".mxf", ".mkv", ".avi", ".m4v")
# разделители титров: в образце используется тире с пробелами
TITLE_SEPS = (" — ", " - ", " – ", "—")


def parse_titles(text: str):
    """«Имя — Должность» по строке на человека. Без должности тоже можно."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        for sep in TITLE_SEPS:
            if sep in line:
                name, role = line.split(sep, 1)
                out.append({"name": name.strip(), "role": role.strip()})
                break
        else:
            out.append({"name": line, "role": ""})
    return out


def scan_broll(folder: str):
    """Видео из папки. Перебивки выбираются по порядку имён, сортировка тут же."""
    p = Path((folder or "").strip().strip('"'))
    if not p.is_dir():
        return []
    return [str(f) for f in sorted(p.iterdir())
            if f.is_file() and f.suffix.lower() in VIDEO_EXT]


class SyncRow(ctk.CTkFrame):
    """Один синхрон: кто говорит, на какой картинке и в каком файле речь."""

    def __init__(self, parent, app, n: int, on_remove):
        super().__init__(parent, fg_color=T.RAISED, corner_radius=8)
        self.grid_columnconfigure(0, weight=1, uniform="s")
        self.grid_columnconfigure(1, weight=1, uniform="s")
        self.grid_columnconfigure(2, weight=2, uniform="s")

        self.speaker = ctk.StringVar(value="" if n > 1 else "")
        self.role = ctk.StringVar()
        self.media = ctk.StringVar()
        self.audio = ctk.StringVar()
        self.transcript = ctk.StringVar()

        pick = app.pick_file
        T.mini_field(self, self.speaker, "Имя спикера").grid(
            row=0, column=0, columnspan=2, sticky="ew", padx=(8, 0),
            pady=(8, 4))
        T.btn(self, "−", "danger", width=34, height=32,
              command=lambda: on_remove(self)).grid(
                  row=0, column=2, sticky="e", padx=(0, 8), pady=(8, 4))
        T.mini_field(self, self.role, "Должность").grid(
            row=1, column=0, sticky="ew", padx=(8, 8), pady=4)
        T.mini_field(self, self.media, "Видео",
                     browse=lambda: pick(self.media, "Видео синхрона",
                                         [("Видео", "*.mp4 *.mov *.mxf *.mkv")])
                     ).grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=4)
        T.mini_field(self, self.audio, "Микрофон",
                     browse=lambda: pick(self.audio, "Микрофон спикера",
                                         [("Звук", "*.wav *.mp3 *.m4a")])
                     ).grid(row=1, column=2, sticky="ew", padx=(0, 8), pady=4)
        T.mini_field(self, self.transcript, "Транскрипт речи (.txt или .json)",
                     browse=lambda: pick(self.transcript, "Транскрипт",
                                         [("Текст", "*.txt *.json")])
                     ).grid(row=2, column=0, columnspan=3, sticky="ew",
                            padx=(8, 8), pady=(4, 8))

    def spec(self):
        """Готовая запись для spec.syncs. Пустые поля в JSON не пишем."""
        d = {}
        for key, var in (("speaker", self.speaker), ("role", self.role),
                         ("media", self.media), ("audio", self.audio),
                         ("transcript", self.transcript)):
            v = var.get().strip().strip('"')
            if v:
                d[key] = v
        return d

    def label(self):
        return self.speaker.get().strip() or "без имени"


def page_news(parent, app):
    """Новостной сюжет: лид, суть, синхроны, детали, финал."""
    wrap = ctk.CTkScrollableFrame(parent, fg_color="transparent")

    # ------------------------------------------------------- синхроны
    syn = T.card(wrap, "Синхроны", "Кого берём в сюжет. Лид программа "
                 "выбирает сама — по цифрам, эмоциям и конфликту в речи")
    box = ctk.CTkFrame(syn, fg_color="transparent")
    box.grid(row=0, column=0, columnspan=3, sticky="ew")

    rows = []
    cnt = ctk.CTkLabel(box, text="", text_color=T.FAINT, anchor="w",
                       font=T.font(11))
    cnt.grid(row=0, column=0, sticky="w", pady=(0, 4))

    def recount():
        cnt.configure(text=f"синхронов: {len(rows)}")

    def add_row():
        r = SyncRow(box, app, len(rows) + 1, remove_row)
        r.grid(row=len(rows) + 1, column=0, sticky="ew", pady=(0, 6))
        rows.append(r)
        recount()

    def remove_row(r):
        rows.remove(r)
        r.destroy()
        for i, rr in enumerate(rows):
            rr.grid_configure(row=i + 1)
        recount()

    add_row()      # одна строка сразу: пустая форма пугает

    bar = ctk.CTkFrame(box, fg_color="transparent")
    bar.grid(row=99, column=0, sticky="w", pady=(2, 4))
    T.btn(bar, "+ Ещё спикер", "ghost", width=150, command=add_row).pack(
        side="left")

    # ------------------------------------------------------- перебивки
    br = T.card(wrap, "Перебивки", "Видео, которым прикрывают речь. "
                 "Под синхрон длиннее 10 с перебивка вставляется сама")
    broll_dir = ctk.StringVar()
    T.field(br, 0, "Папка с перебивками", broll_dir, "берём все видео из папки",
            browse=lambda: app.pick_dir(broll_dir, "Папка с перебивками"))
    br_info = ctk.CTkLabel(br, text="", text_color=T.MUTED, anchor="w",
                           font=T.font(11))
    br_info.grid(row=1, column=0, columnspan=3, sticky="w")

    def show_broll():
        n = len(scan_broll(broll_dir.get()))
        br_info.configure(text=f"найдено перебивок: {n}" if n
                          else "папка не указана или видео в ней нет",
                          text_color=T.SUCCESS if n else T.MUTED)

    broll_dir.trace_add("write", lambda *_: show_broll())
    show_broll()

    # ------------------------------------------------------- закадр
    vo = T.card(wrap, "Закадр", "Голос ведущего под перебивками. "
                 "Без него блоки «суть» и «детали» останутся на перебивках")
    vo_audio = ctk.StringVar()
    T.field(vo, 0, "Дорожка закадра", vo_audio, "необязательно",
            browse=lambda: app.pick_file(vo_audio, "Закадр",
                                         [("Звук", "*.wav *.mp3 *.m4a")]))

    # ------------------------------------------------------- стендап
    st = T.card(wrap, "Стендап для финала", "Необязательно. Без него финал "
                 "возьмёт свободный синхрон, который ещё не звучал")
    st_media = ctk.StringVar()
    st_audio = ctk.StringVar()
    st_tx = ctk.StringVar()
    T.field(st, 0, "Видео стендапа", st_media, "необязательно",
            browse=lambda: app.pick_file(st_media, "Видео стендапа"))
    T.field(st, 1, "Микрофон стендапа", st_audio, "необязательно",
            browse=lambda: app.pick_file(st_audio, "Микрофон стендапа"))
    T.field(st, 2, "Транскрипт стендапа", st_tx, "необязательно",
            browse=lambda: app.pick_file(st_tx, "Транскрипт стендапа",
                                         [("Текст", "*.txt *.json")]))

    # ------------------------------------------------------- титры
    ti = T.card(wrap, "Титры", "По строке на человека: Имя — Должность. "
                 "Попадают в titles.csv")
    titles_txt = ctk.CTkTextbox(ti, height=110, corner_radius=8,
                                fg_color=T.RAISED, border_color=T.BORDER,
                                border_width=1, text_color=T.TEXT,
                                font=T.font(13), wrap="word")
    titles_txt.grid(row=0, column=0, columnspan=3, sticky="ew")

    # ------------------------------------------------------- результат
    dst = T.card(wrap, "Результат")
    out = ctk.StringVar()
    T.field(dst, 0, "Папка проекта", out,
            browse=lambda: app.pick_dir(out, "Куда сложить"))

    note = ctk.CTkLabel(wrap, text="", text_color=T.SUCCESS, anchor="w",
                        font=T.font(12))
    note.pack(anchor="w", padx=T.PAD, pady=(0, 10))

    # ------------------------------------------------------- сборка
    def collect():
        """Собирает spec и возвращает (spec, замечания)."""
        bad = []
        syncs = []
        for r in rows:
            d = r.spec()
            if not d:
                continue
            if not d.get("media"):
                bad.append(f"У синхрона «{r.label()}» не указано видео.")
            elif not Path(d["media"]).is_file():
                bad.append(f"Видео синхрона «{r.label()}» не найдено: "
                           f"{d['media']}")
            if d.get("transcript") and not Path(d["transcript"]).is_file():
                bad.append(f"Транскрипт синхрона «{r.label()}» не найден: "
                           f"{d['transcript']}")
            syncs.append(d)

        if not syncs:
            bad.append("Заполни хотя бы одного синхрона: без них сюжет "
                       "не собрать.")
        else:
            # без транскрипта нечем оценить реплики и выбрать лид - ошибка
            # в середине работы, дешевле сказать сразу
            if not any(s.get("transcript") for s in syncs):
                bad.append("Нужен транскрипт хотя бы у одного синхрона: "
                           "по нему выбирается лид и режутся паузы.")

        spec = {"syncs": syncs}
        broll = scan_broll(broll_dir.get())
        if broll:
            spec["broll"] = broll
        va = vo_audio.get().strip().strip('"')
        if va:
            spec["vo"] = {"audio": va}
        standup = {}
        for key, var in (("media", st_media), ("audio", st_audio),
                         ("transcript", st_tx)):
            v = var.get().strip().strip('"')
            if v:
                standup[key] = v
        # стендап без транскрипта бесполезен: код всё равно ищет лучшую
        # реплику в нём и молча оставит финал пустым
        if standup.get("media") and not standup.get("transcript"):
            bad.append("У стендапа указан видеофайл, но не транскрипт — "
                       "финал не наберётся.")
        elif standup.get("media"):
            spec["standup"] = standup
        titles = parse_titles(titles_txt.get("1.0", "end"))
        if titles:
            spec["titles"] = titles
        return spec, bad

    def run():
        spec, bad = collect()
        if bad:
            app.warn("\n".join(bad[:6]))
            return
        o = out.get().strip().strip('"') or str(BASE / "news_out")
        Path(o).mkdir(parents=True, exist_ok=True)
        spec_path = Path(o) / "spec.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        rz = f"{o}/rz"
        app.go([
            ("1. Раскладка сюжета",
             [str(MODES / "news_package.py"), "--spec", str(spec_path),
              "--outdir", rz]),
            ("2. XML для Premiere",
             [str(IO / "premiere.py"), "--timeline", f"{rz}/selects.csv",
              "--media-dir", o, "--out", f"{o}/ryba.xml",
              "--edl", f"{o}/ryba.edl", "--trim-rec"]),
            ("3. Субтитры",
             [str(IO / "srt.py"), "--selects", f"{rz}/selects.csv",
              "--out", f"{o}/ryba.srt"]),
        ], o)

    # ------------------------------------------------------- кнопки
    row = ctk.CTkFrame(wrap, fg_color="transparent")
    row.pack(fill="x", padx=T.PAD, pady=(0, T.PAD))
    T.btn(row, "Собрать сюжет", "primary", width=200, command=run).pack(
        side="left")

    spec_example = SRC.parent / "assets" / "news_spec_example.json"
    if spec_example.is_file():
        T.btn(row, "Открыть образец spec.json", "quiet",
              command=lambda: os.startfile(str(spec_example))
              ).pack(side="left", padx=10)
    return wrap

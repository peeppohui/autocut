# -*- coding: utf-8 -*-
"""Разбор готового монтажа: кадры, дорожки, ручная правка.

Панель встраивается в страницы сборки («Интервью 2 камеры»,
«Новостной сюжет», «Дайджест», «Интервью 1 видео»), а не живёт
отдельным пунктом в меню. Причина простая: монтаж рождается там, и
смотреть на него нужно там же. Отдельная страница требовала бы
вспоминать, в какой папке лежит результат, и лишнего клика.

Что умеет:
  - кадр каждого куска с подписью (как контактный лист в Premiere);
  - дорожки V1/V2 и звука с линейкой времени;
  - выкинуть кусок, поменять ракурсы, пересобрать XML;
  - собрать превью.mp4 и открыть его системным плеером.

Про tkinter.Canvas: у customtkinter нет полотна, поэтому дорожки
рисуются на канве. Цвета берутся из theme.py, фон и рамки совпадают
с остальным окном.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import tkinter as tk

import customtkinter as ctk

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
IO = SRC / "io"

sys.path.insert(0, str(SRC))

import theme as T                            # noqa: E402
import timeline as TL                        # noqa: E402

LANE_H = 34
RULER_H = 22
LANE_GAP = 4
MIN_PPS = 2.0
MAX_PPS = 80.0
DEFAULT_PPS = 14.0
CLIP_MIN_W = 3
PANEL_H = 470          # чтобы страница со формой и разбором влезали вместе


class ResultPanel(ctk.CTkFrame):
    """Показывает и правит монтаж, собранный на этой же странице."""

    def __init__(self, parent, app, project_var=None, stacked=None):
        super().__init__(parent, fg_color=T.SURFACE, corner_radius=T.R,
                         height=PANEL_H)
        self.app = app
        self.project_var = project_var      # поле «куда сложить» на странице
        self.stacked_var = stacked          # переключатель второго ракурса
        self.grid_propagate(False)

        self.rows = []
        self.fields = []
        self.csv_path = None
        self.project = None
        self.pps = DEFAULT_PPS
        self.sel = None
        self.dirty = False
        self.boxes = []
        self._keep = []

        self._build()
        app.register_panel(self)

    # ------------------------------------------------------------ сборка
    def _build(self):
        self.dir_var = ctk.StringVar()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=T.PAD, pady=(14, 6))
        ctk.CTkLabel(head, text="Готовый монтаж", text_color=T.TEXT,
                     anchor="w", font=T.font(15, "bold")).pack(side="left")
        self.info = ctk.CTkLabel(head, text="Монтаж ещё не собран",
                                 text_color=T.MUTED, anchor="e",
                                 font=T.font(12))
        self.info.pack(side="right")

        # отдельная строка с путём: без неё нельзя открыть проект,
        # собранный в прошлый раз
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", padx=T.PAD, pady=(0, 8))
        ctk.CTkEntry(row, textvariable=self.dir_var, height=34,
                     corner_radius=8, fg_color=T.RAISED,
                     border_color=T.BORDER, border_width=1,
                     text_color=T.TEXT, font=T.font(13),
                     placeholder_text=r"папка проекта — пусто, возьмём из "
                                       r"поля «Куда сложить» выше",
                     placeholder_text_color=T.MUTED).pack(
                         side="left", fill="x", expand=True, padx=(0, 8))
        T.btn(row, "Показать", "ghost", width=110,
              command=lambda: self.load()).pack(side="left")
        T.btn(row, "Собрать превью", "ghost", width=150,
              command=lambda: self.build_preview()).pack(side="left", padx=8)
        T.btn(row, "Открыть папку", "quiet", width=130,
              command=lambda: self.open_dir()).pack(side="left")

        self.tabs = ctk.CTkSegmentedButton(
            self, values=["Кадры", "Дорожки"],
            command=lambda v: self.show_tab(v),
            font=T.font(13), height=32, corner_radius=8,
            fg_color=T.RAISED, selected_color=T.ACCENT,
            selected_hover_color="#7aa2ff", unselected_color=T.RAISED,
            unselected_hover_color=T.BORDER, text_color=T.TEXT)
        self.tabs.set("Кадры")
        self.tabs.grid(row=2, column=0, sticky="ew", padx=T.PAD, pady=(0, 8))

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=3, column=0, sticky="nsew", padx=T.PAD)
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)

        self.sheet = ctk.CTkScrollableFrame(self.body, fg_color="transparent")
        self.sheet.grid(row=0, column=0, sticky="nsew")

        self.tracks = ctk.CTkFrame(self.body, fg_color="transparent")
        self.tracks.grid(row=0, column=0, sticky="nsew")
        self.tracks.grid_columnconfigure(0, weight=1)
        self.tracks.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(self.tracks, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        for txt, cmd, wid in (("−", lambda: self.zoom(-1), 36),
                              ("+", lambda: self.zoom(1), 36)):
            T.btn(bar, txt, "ghost", width=wid,
                  command=cmd).pack(side="left", padx=(0, 6))
        self.zoom_lbl = ctk.StringVar(value="")
        ctk.CTkLabel(bar, textvariable=self.zoom_lbl, text_color=T.MUTED,
                     width=100, anchor="w", font=T.font(12)).pack(side="left")
        T.btn(bar, "Обновить", "quiet", width=110,
              command=lambda: self.load()).pack(side="right")

        hold = ctk.CTkFrame(self.tracks, fg_color="transparent")
        hold.grid(row=1, column=0, sticky="nsew")
        vscroll = ctk.CTkScrollableFrame(hold, fg_color="transparent")
        vscroll.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(vscroll, bg=T.BG, highlightthickness=1,
                                highlightbackground=T.BORDER, bd=0)
        self.canvas.pack(anchor="w")
        self.hbar = ctk.CTkScrollbar(hold, orientation="horizontal",
                                     command=self.canvas.xview)
        self.hbar.pack(fill="x", pady=(4, 0))
        self.canvas.bind("<Button-1>", self._on_click)

        acts = ctk.CTkFrame(self.tracks, fg_color="transparent")
        acts.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.b_drop = T.btn(acts, "Выкинуть кусок", "danger", width=160,
                            command=lambda: self.drop(), state="disabled")
        self.b_drop.pack(side="left")
        self.b_swap = T.btn(acts, "Поменять ракурсы", "ghost", width=180,
                            command=lambda: self.swap(), state="disabled")
        self.b_swap.pack(side="left", padx=8)
        self.b_save = T.btn(acts, "Сохранить и пересобрать", "primary",
                            width=230, command=lambda: self.save_rebuild())
        self.b_save.pack(side="right")

        self.clip_info = ctk.StringVar(value="Ничего не выбрано")
        ctk.CTkLabel(acts, textvariable=self.clip_info, text_color=T.MUTED,
                     anchor="w", font=T.font(12)).pack(side="left", padx=10)

        self.show_tab("Кадры")

    def show_tab(self, which):
        self.sheet.grid() if which == "Кадры" else self.sheet.grid_remove()
        self.tracks.grid() if which == "Дорожки" else self.tracks.grid_remove()

    # ------------------------------------------------------------ загрузка
    def _project(self):
        raw = (self.dir_var.get() or "").strip().strip('"')
        if raw:
            p = Path(raw)
        elif self.project_var is not None:
            p = Path((self.project_var.get() or "").strip().strip('"') or ".")
        else:
            return None
        return p if p.is_dir() else None

    def load(self):
        """Перечитывает раскладку и перерисовывает обе вкладки."""
        self._keep.clear()
        p = self._project()
        if p is None:
            self.info.configure(text="Укажи папку проекта",
                                text_color=T.MUTED)
            return False
        csv_path = TL.find_csv(p)
        if csv_path is None:
            self.info.configure(text=f"В {p.name} нет раскладки "
                                     "(selects.csv или timeline.csv)",
                                text_color=T.MUTED)
            return False
        try:
            self.rows, self.fields = TL.read_rows(csv_path)
        except Exception as e:
            self.info.configure(text=f"Не прочитал раскладку: {e}",
                                text_color=T.DANGER)
            return False
        self.csv_path = csv_path
        self.project = p
        self.dirty = False
        self.sel = None
        self._set_sel(None)

        tl = TL.build_timeline(self.rows)
        xml = p / "ryba.xml"
        extra = " · ryba.xml есть" if xml.is_file() else " · XML ещё не собран"
        self.info.configure(
            text=f"{len(self.rows)} кусков · {TL.mmss(tl['total'])}{extra}",
            text_color=T.SUCCESS)
        self._fill_sheet()
        self.redraw_tracks()
        return True

    def _fill_sheet(self):
        """Кадр каждого куска. make_thumbs кладёт путь в поле thumb."""
        for w in self.sheet.winfo_children():
            w.destroy()
        self._keep.clear()
        if not self.rows:
            ctk.CTkLabel(self.sheet, text="Пусто", text_color=T.MUTED,
                         font=T.font(13)).pack(anchor="w", pady=10)
            return
        for c in range(4):
            self.sheet.grid_columnconfigure(c, weight=1, uniform="t")
        try:
            import preview as pv
            pv.make_thumbs(self.rows, self.csv_path.parent / "thumbs")
        except Exception:
            pass        # без кадров панель всё равно полезна: дорожки есть

        for k, r in enumerate(self.rows):
            cell = ctk.CTkFrame(self.sheet, fg_color=T.RAISED,
                                corner_radius=8)
            cell.grid(row=k // 4, column=k % 4, sticky="nsew",
                      padx=3, pady=3)
            pic = ctk.CTkLabel(cell, text="", height=78, corner_radius=6,
                               fg_color=T.BG)
            pic.pack(fill="x", padx=6, pady=(6, 4))
            thumb = r.get("thumb")
            if thumb and Path(thumb).is_file():
                try:
                    im = tk.PhotoImage(file=str(thumb))
                    if im.width() > 160:
                        im = im.subsample(max(1, im.width() // 160))
                    self._keep.append(im)
                    pic.configure(image=im)
                except Exception:
                    pass
            ctk.CTkLabel(cell, text=f"{(r.get('hero') or '-')} · "
                                    f"{r.get('duration', 0):.1f}с",
                         text_color=T.ACCENT, anchor="w",
                         font=T.font(11, "bold")).pack(anchor="w", padx=6)
            ctk.CTkLabel(cell, text=(r.get("desc") or "")[:60],
                         text_color=T.MUTED, anchor="w", font=T.font(11),
                         wraplength=140, justify="left").pack(anchor="w",
                                                              padx=6,
                                                              pady=(0, 6))

    # ------------------------------------------------------------ рисование
    def zoom(self, step):
        self.pps = max(MIN_PPS, min(MAX_PPS,
                                    self.pps * (1.25 if step > 0 else 0.8)))
        self.redraw_tracks()

    def redraw_tracks(self):
        # имя НЕ _draw: такой метод есть у ctk.CTkFrame, и перекрытие
        # ломает отрисовку самого виджета с TypeError
        self.canvas.delete("all")
        self.boxes = []
        tl = TL.build_timeline(self.rows)
        self.zoom_lbl.set(f"{self.pps:.0f} пикс/сек")
        if not tl["lanes"]:
            self.canvas.configure(width=500, height=40)
            self.canvas.create_text(10, 20, anchor="w", text="Пусто",
                                    fill=T.MUTED, font=("Segoe UI", 12))
            return
        pps = self.pps
        w = max(380, int(tl["total"] * pps) + 40)
        h = RULER_H + len(tl["lanes"]) * (LANE_H + LANE_GAP)
        self.canvas.configure(width=w, height=h + 6)
        self.canvas.configure(scrollregion=(0, 0, w, h + 6))
        self.hbar.configure(command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=self.hbar.set)

        step = 5 if pps >= 8 else (10 if pps >= 3 else 30)
        t = 0.0
        while t <= tl["total"]:
            x = t * pps
            self.canvas.create_line(x, 0, x, h, fill=T.BORDER)
            self.canvas.create_text(x + 3, 2, anchor="nw", text=TL.mmss(t),
                                    fill=T.FAINT, font=("Segoe UI", 9))
            t += step
        self.canvas.create_text(w - 4, 2, anchor="ne",
                                text=f"всего {TL.mmss(tl['total'])}",
                                fill=T.MUTED, font=("Segoe UI", 10))

        for li, lane in enumerate(tl["lanes"]):
            y = RULER_H + li * (LANE_H + LANE_GAP)
            fill = T.RAISED if lane["kind"] == "alt" else T.BG
            self.canvas.create_rectangle(0, y, w, y + LANE_H, fill=fill,
                                         outline="")
            self.canvas.create_text(6, y + LANE_H // 2, anchor="w",
                                    text=lane["name"], fill=T.FAINT,
                                    font=("Segoe UI", 9))
            for it in lane["items"]:
                x0 = it["start"] * pps
                x1 = x0 + max(CLIP_MIN_W, it["dur"] * pps)
                row = self.rows[it["row"]]
                tag = f"r{it['row']}"
                self.canvas.create_rectangle(
                    x0 + 2, y + 3, x1 - 2, y + LANE_H - 3,
                    fill=self._color(lane["kind"], it["role"]),
                    outline=T.BORDER, width=1, tags=("clip", tag))
                if x1 - x0 > 46:
                    lbl = (row.get("hero") or row.get("desc")
                           or row.get("file") or "")
                    # подпись получает те же теги: она рисуется поверх
                    # прямоугольника, и клик по буквам тоже должен выбрать
                    # кусок, иначе он «не нажимается» в середине
                    self.canvas.create_text(
                        x0 + 7, y + LANE_H // 2, anchor="w",
                        text=lbl[:max(1, int((x1 - x0) / 7))],
                        fill="#ffffff", font=("Segoe UI", 9),
                        tags=("clip", tag))
        self.canvas.tag_raise("clip")

    @staticmethod
    def _color(kind, role):
        if kind == "alt":
            return T.ACCENT_DIM
        if kind == "audio":
            return "#2a4a3a" if role == "linked" else "#3a3550"
        return T.ACCENT if role == "main" else T.ACCENT_DIM

    # ------------------------------------------------------------ выбор
    def _on_click(self, ev):
        item = self.canvas.find_closest(ev.x, ev.y)[0]
        for tag in self.canvas.gettags(item):
            if tag.startswith("r"):
                self._set_sel(int(tag[1:]))
                return
        self._set_sel(None)

    def _set_sel(self, idx):
        self.sel = idx
        if idx is None:
            self.b_drop.configure(state="disabled")
            self.b_swap.configure(state="disabled")
            self.clip_info.set("Ничего не выбрано")
            return
        self.b_drop.configure(state="normal")
        row = self.rows[idx]
        self.b_swap.configure(state="normal" if TL.has_alt(row)
                              else "disabled")
        self.clip_info.set(
            f"кусок {idx + 1}/{len(self.rows)} · {row.get('duration', 0):.1f}с"
            f" · {(row.get('hero') or '-')}")

    # ------------------------------------------------------------ правка
    def drop(self):
        if self.sel is None:
            return
        self.rows.pop(self.sel)
        self.sel = None
        self.dirty = True
        self._set_sel(None)
        self._refresh_counts()
        self.redraw_tracks()

    def swap(self):
        if self.sel is None:
            return
        if TL.swap_angle(self.rows[self.sel]):
            self.dirty = True
            self._set_sel(self.sel)
            self.redraw_tracks()

    def _refresh_counts(self):
        tl = TL.build_timeline(self.rows)
        self.info.configure(text=f"{len(self.rows)} кусков · "
                                 f"{TL.mmss(tl['total'])} · не сохранено",
                            text_color=T.ACCENT)

    # ------------------------------------------------------------ выход
    def open_dir(self):
        d = self._project() or (self.csv_path.parent if self.csv_path
                                else None)
        if d:
            try:
                os.startfile(str(d))
            except Exception:
                pass

    def build_preview(self):
        if self.csv_path is None and not self.load():
            self.app.warn("Сначала укажи папку проекта.")
            return
        out_mp4 = self.project / "preview.mp4"
        self.app.go([("Сборка превью",
                      [str(IO / "preview.py"), "--selects",
                       str(self.csv_path), "--out", str(out_mp4)])],
                    str(self.project))

    def save_rebuild(self):
        if self.csv_path is None:
            self.app.warn("Сначала покажи раскладку.")
            return
        if not self.dirty:
            self.app.warn("Правок нет — нечего сохранять.")
            return
        try:
            TL.write_rows(self.csv_path, self.rows, self.fields)
        except Exception as e:
            self.app.warn(f"Не сохранил раскладку: {e}")
            return
        self.dirty = False
        project = self.project
        argv = [str(IO / "premiere.py"), "--timeline", str(self.csv_path),
                "--media-dir", str(project), "--out", str(project / "ryba.xml"),
                "--edl", str(project / "ryba.edl"), "--trim-rec"]
        if self.stacked_var is not None and self.stacked_var.get():
            argv.append("--stacked")
        steps = [("1. XML для Premiere", argv)]
        srt = project / "ryba.srt"
        if srt.is_file():
            # хронометраж после правки поехал, старые субтитры больше
            # не совпадают с картинкой
            steps.append(("2. Субтитры",
                          [str(IO / "srt.py"), "--selects",
                           str(self.csv_path), "--out", str(srt)]))
        self.app.log(f"Раскладка сохранена, копия: {self.csv_path}.bak")
        self.app.go(steps, str(project))


def add_to(page, app, project_var=None, stacked=None):
    """Ставит панель в конец прокручиваемой страницы сборки.

    Панель всегда на виду, отдельной кнопки «открыть разбор» нет:
    если прятать её за кнопкой, монтаж придётся искать.
    """
    panel = ResultPanel(page, app, project_var, stacked)
    panel.pack(fill="x", padx=0, pady=(0, T.PAD))
    return panel

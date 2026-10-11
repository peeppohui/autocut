# -*- coding: utf-8 -*-
"""Демо оформления AutoCut на customtkinter.

Это НЕ рабочее окно. Здесь показан только вид: палитра, отступы,
типографика, карточки и элементы управления. Рабочее окно живёт в
app.py и его не трогает.

Запуск:   python plugin/src/ui/demo_ctk.py
Проверка без окна (собирает и сразу закрывает):
          python plugin/src/ui/demo_ctk.py --selftest
"""
from __future__ import annotations

import sys

import customtkinter as ctk

# ---------------------------------------------------------------- палитра
# Дефолтная синяя тема customtkinter выглядит как чужой инструмент.
# Свой набор цветов, чтобы окно выглядело частью проекта, а не сборкой.
BG = "#0f1115"          # фон окна
SURFACE = "#171a21"     # карточка
RAISED = "#1e222b"      # поле, приподнятое
BORDER = "#2a2f3a"
TEXT = "#e6e9ef"
MUTED = "#8b93a3"       # подписи, подсказки
ACCENT = "#5b8cff"      # главное действие
ACCENT_DIM = "#2f4a8f"
SUCCESS = "#3ecf8e"
DANGER = "#f2555a"

R = 10                  # единый радиус скругления
PAD = 20                # единый отступ

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def btn(parent, text, kind="ghost", **kw):
    """Кнопки одного семейства: главная, второстепенная, тихая."""
    looks = {
        "primary": dict(fg_color=ACCENT, hover_color="#7aa2ff",
                        text_color="#0b1020", height=42, corner_radius=R),
        "ghost": dict(fg_color=RAISED, hover_color=BORDER,
                      text_color=TEXT, height=36, corner_radius=8),
        "quiet": dict(fg_color="transparent", hover_color=RAISED,
                      text_color=MUTED, height=30, corner_radius=8),
    }
    return ctk.CTkButton(parent, text=text, **looks[kind], **kw)


def card(parent, title, subtitle=""):
    """Карточка с заголовком. Возвращает внутренний фрейм для полей."""
    box = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=R)
    # box обязан получить геометрию сам: если этого не сделать, он остаётся
    # 1x1, и всё содержимое карточки схлопывается вместе с ним
    box.pack(fill="x", padx=PAD, pady=(0, 16))
    head = ctk.CTkFrame(box, fg_color="transparent")
    head.pack(fill="x", padx=PAD, pady=(PAD, 0))
    ctk.CTkLabel(head, text=title, text_color=TEXT, anchor="w",
                 font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w")
    if subtitle:
        ctk.CTkLabel(head, text=subtitle, text_color=MUTED, anchor="w",
                     font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(2, 0))
    body = ctk.CTkFrame(box, fg_color="transparent")
    # внутри прокрутки expand=True схлопывает карточку в 1x1: высоту
    # прокручиваемая рамка берёт сама, по содержимому
    body.pack(fill="x", padx=PAD, pady=(14, PAD))
    body.grid_columnconfigure(1, weight=1)
    return body


def field(body, row, label, var, placeholder=""):
    """Строка «подпись — поле — кнопка»."""
    ctk.CTkLabel(body, text=label, text_color=MUTED, anchor="w",
                 width=170, font=ctk.CTkFont(size=13)).grid(
                     row=row, column=0, sticky="w", pady=5, padx=(0, 14))
    ctk.CTkEntry(body, textvariable=var, height=36, corner_radius=8,
                 fg_color=RAISED, border_color=BORDER, border_width=1,
                 text_color=TEXT, font=ctk.CTkFont(size=13),
                 placeholder_text=placeholder).grid(
                     row=row, column=1, sticky="ew", pady=5)
    btn(body, "…", "ghost", width=36).grid(row=row, column=2, padx=(8, 0))


def switch(body, row, label, var, hint=""):
    ctk.CTkSwitch(body, text=label, variable=var, progress_color=ACCENT,
                  button_color=TEXT, button_hover_color="#ffffff",
                  text_color=TEXT, font=ctk.CTkFont(size=13),
                  width=46, height=24).grid(row=row, column=0, columnspan=2,
                                            sticky="w", pady=6)
    if hint:
        ctk.CTkLabel(body, text=hint, text_color=MUTED, anchor="w",
                     font=ctk.CTkFont(size=11)).grid(
                         row=row + 1, column=0, columnspan=3, sticky="w")


def slider(body, row, label, var, lo, hi, suffix=""):
    ctk.CTkLabel(body, text=label, text_color=MUTED, anchor="w",
                 font=ctk.CTkFont(size=13)).grid(row=row, column=0, sticky="w")
    val = ctk.CTkLabel(body, text="", text_color=TEXT, anchor="e", width=56,
                       font=ctk.CTkFont(size=13, weight="bold"))

    def show(*_):
        v = var.get()
        val.configure(text=f"{v:.2f}".rstrip("0").rstrip(".") + suffix)

    var.trace_add("write", show)
    show()
    ctk.CTkSlider(body, from_=lo, to=hi, number_of_steps=100,
                  variable=var, height=14, button_color=ACCENT,
                  button_hover_color="#7aa2ff", progress_color=ACCENT_DIM,
                  fg_color=BORDER
                  ).grid(row=row, column=1, sticky="ew", padx=14, pady=6)
    val.grid(row=row, column=2, sticky="e")


# ---------------------------------------------------------------- страницы
def page_interview(parent):
    wrap = ctk.CTkScrollableFrame(parent, fg_color="transparent")
    wide = ctk.StringVar()
    gcam = ctk.StringVar()
    gmic = ctk.StringVar()
    hmic = ctk.StringVar()
    out = ctk.StringVar(value="E:\\монтаж\\проект")
    tgt = ctk.StringVar()
    two_cam = ctk.BooleanVar(value=True)
    only_tx = ctk.BooleanVar(value=False)

    src = card(wrap, "Исходники", "Синхронизируются между собой по звуку")
    field(src, 0, "Общий план", wide, "camera_wide.mov")
    field(src, 1, "Камера гостя", gcam, "camera_guest.mov")
    field(src, 2, "Микрофон гостя", gmic, "_guest.wav")
    field(src, 3, "Микрофон ведущего", hmic, "_host.wav")

    opt = card(wrap, "Монтаж")
    ctk.CTkLabel(opt, text="Хронометраж (ММ:СС)", text_color=MUTED, anchor="w",
                 width=170, font=ctk.CTkFont(size=13)).grid(
                     row=0, column=0, sticky="w", pady=5, padx=(0, 14))
    ctk.CTkEntry(opt, textvariable=tgt, width=110, height=36, corner_radius=8,
                 fg_color=RAISED, border_color=BORDER, border_width=1,
                 text_color=TEXT, placeholder_text="пусто — не ограничивать",
                 font=ctk.CTkFont(size=13)).grid(row=0, column=1, sticky="w", pady=5)
    switch(opt, 1, "Второй ракурс на отдельной дорожке", two_cam,
           "Premiere не понимает multiclip — две дорожки работают всегда")
    switch(opt, 3, "Сначала только транскрипт", only_tx,
           "правлю текст вручную, потом собираю рыбу")

    dst = card(wrap, "Результат")
    field(dst, 0, "Папка проекта", out)

    row = ctk.CTkFrame(wrap, fg_color="transparent")
    row.pack(fill="x", padx=PAD, pady=(0, PAD))
    btn(row, "Собрать рыбу", "primary", width=180).pack(side="left")
    btn(row, "Собрать из правленого текста", "ghost").pack(side="left", padx=10)
    return wrap


def page_settings(parent):
    wrap = ctk.CTkScrollableFrame(parent, fg_color="transparent")

    cut = card(wrap, "Резка речи", "Пороги из settings.json — здесь без правки файла")
    max_pause = ctk.DoubleVar(value=0.60)
    merge_gap = ctk.DoubleVar(value=0.80)
    min_keep = ctk.DoubleVar(value=1.00)
    min_prob = ctk.DoubleVar(value=0.25)
    slider(cut, 0, "Пауза до разрыва, с", max_pause, 0.1, 3.0, " с")
    slider(cut, 1, "Склеивать разрывы до, с", merge_gap, 0.1, 3.0, " с")
    slider(cut, 2, "Минимальный кусок, с", min_keep, 0.2, 5.0, " с")
    slider(cut, 3, "Порог уверенности слов", min_prob, 0.05, 0.80)

    shot = card(wrap, "Камера")
    shot_min = ctk.DoubleVar(value=2.0)
    shot_max = ctk.DoubleVar(value=12.0)
    share_a = ctk.DoubleVar(value=0.70)
    slider(shot, 0, "Короткий кадр, с", shot_min, 1.0, 6.0, " с")
    slider(shot, 1, "Длинный кадр, с", shot_max, 5.0, 30.0, " с")
    slider(shot, 2, "Доля основной камеры", share_a, 0.1, 1.0)

    sw = card(wrap, "Поведение")
    switch(sw, 0, "Отсекать куски без картинки", ctk.BooleanVar(value=True),
           "камера не включена — выкидываем молчание вместо речи")
    switch(sw, 2, "Прикрывать стыки перебивками", ctk.BooleanVar(value=False),
           "иначе на стыках будет прыжок кадра")

    btn(wrap, "Сохранить настройки", "primary", width=200).pack(
        anchor="w", padx=PAD, pady=(0, PAD))
    return wrap


def page_log(parent):
    box = ctk.CTkTextbox(parent, fg_color=SURFACE, corner_radius=R,
                         border_color=BORDER, border_width=1,
                         text_color=MUTED, font=ctk.CTkFont(
                             family="Consolas", size=12))
    box.pack(fill="both", expand=True, padx=PAD, pady=PAD)
    box.insert("1.0", "Здесь будет ход работы.\n")
    box.configure(state="disabled")
    return box


# ---------------------------------------------------------------- окно
class Demo(ctk.CTk):
    PAGES = [("Интервью 2 камеры", page_interview),
             ("Настройки", page_settings),
             ("Ход работы", page_log)]

    def __init__(self):
        super().__init__()
        self.title("AutoCut — демо оформления")
        self.geometry("940x700")
        self.configure(fg_color=BG)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._nav = []

        side = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0,
                            width=232)
        side.grid(row=0, column=0, sticky="ns")
        side.grid_propagate(False)
        ctk.CTkLabel(side, text="AutoCut", text_color=TEXT, anchor="w",
                     font=ctk.CTkFont(size=21, weight="bold")).pack(
                         anchor="w", padx=PAD, pady=(24, 2))
        ctk.CTkLabel(side, text="демо оформления", text_color=MUTED, anchor="w",
                     font=ctk.CTkFont(size=11)).pack(anchor="w", padx=PAD,
                                                      pady=(0, 26))
        for name, _ in self.PAGES:
            b = ctk.CTkButton(side, text=name, anchor="w", height=38,
                              corner_radius=8, fg_color="transparent",
                              hover_color=RAISED, text_color=MUTED,
                              font=ctk.CTkFont(size=13), command=(
                                  lambda n=name: self.show(n)))
            b.pack(fill="x", padx=10, pady=2)
            self._nav.append(b)

        self.body = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.body.grid(row=0, column=1, sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        # раздаём вес строке с содержимым (row=2), а не подзаголовку
        self.body.grid_rowconfigure(2, weight=1)

        self.title_lbl = ctk.CTkLabel(self.body, text="", text_color=TEXT,
                                      anchor="w",
                                      font=ctk.CTkFont(size=23, weight="bold"))
        self.title_lbl.grid(row=0, column=0, sticky="w", padx=PAD,
                            pady=(PAD + 6, 2))
        self.sub_lbl = ctk.CTkLabel(self.body, text="", text_color=MUTED,
                                    anchor="w", font=ctk.CTkFont(size=12))
        self.sub_lbl.grid(row=1, column=0, sticky="w", padx=PAD, pady=(0, 8))

        self.host = ctk.CTkFrame(self.body, fg_color="transparent")
        self.host.grid(row=2, column=0, sticky="nsew")
        self.host.grid_columnconfigure(0, weight=1)
        self.host.grid_rowconfigure(0, weight=1)

        SUBS = {"Интервью 2 камеры": "Две камеры, два микрофона, сборка в Premiere",
                "Настройки": "Пороги чистки речи и раскладки кадров",
                "Ход работы": "Что делает программа прямо сейчас"}
        self.show("Интервью 2 камеры", SUBS["Интервью 2 камеры"])

    def show(self, name, sub=None):
        for w in self.host.winfo_children():
            w.destroy()
        self.title_lbl.configure(text=name)
        self.sub_lbl.configure(text=sub or "")
        for b, (n, _) in zip(self._nav, self.PAGES):
            b.configure(fg_color=RAISED if n == name else "transparent",
                        text_color=TEXT if n == name else MUTED)
        # страницу ищем по имени, а не по переменной цикла: иначе после
        # цикла остаётся последняя и открывается всегда одна и та же
        page = next(f for n, f in self.PAGES if n == name)
        page(self.host).grid(row=0, column=0, sticky="nsew")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    app = Demo()
    if "--selftest" in argv:
        # строим все страницы и закрываемся: ловим ошибки без окна
        for name, fn in Demo.PAGES:
            app.show(name)
            app.update_idletasks()
        app.destroy()
        print("DEMO OK: все страницы собрались")
        return 0
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
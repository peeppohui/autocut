# -*- coding: utf-8 -*-
"""Демо оформления AutoCut — витрина ui/theme.py.

Рабочее окно живёт в app.py, это его демонстрация: та же палитра и те
же элементы, но с выдуманными данными. Нужна, чтобы посмотреть на
элементы в отрыве от пайплайна — например, прикрутить новый виджет и
сразу оценить, как он сядет.

Всё оформление берётся из theme.py. Демка НЕ держит свою копию
палитры: две копии через месяц разъезжаются, и в окне появляются
элементы разного цвета.

Запуск:   python plugin/src/ui/demo_ctk.py
Проверка: python plugin/src/ui/demo_ctk.py --selftest
"""
from __future__ import annotations

import sys
from pathlib import Path

import customtkinter as ctk

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import theme as T  # noqa: E402  — после sys.path, тема лежит рядом

R, PAD = T.R, T.PAD
SUBS = {
    "Интервью 2 камеры": "Две камеры, два микрофона, сборка в Premiere",
    "Просмотр": "Контактный лист рыбы",
    "Настройки": "Пороги чистки речи и раскладки кадров",
    "Заглушка": "Страница будущего шаблона",
}


def page_form(parent):
    wrap = ctk.CTkScrollableFrame(parent, fg_color="transparent")
    src = T.card(wrap, "Исходники", "Синхронизируются между собой по звуку")
    v = ctk.StringVar()
    T.field(src, 0, "Общий план", v, "camera_wide.mov")
    T.field(src, 1, "Камера гостя", ctk.StringVar(), "camera_guest.mov")
    T.field(src, 2, "Микрофон гостя", ctk.StringVar(), "_guest.wav")

    opt = T.card(wrap, "Монтаж")
    T.field(opt, 0, "Хронометраж (ММ:СС)", ctk.StringVar(),
            "пусто — не ограничивать")
    T.switch(opt, 1, "Второй ракурс на отдельной дорожке",
             ctk.BooleanVar(value=True),
             "Premiere не понимает multiclip — две дорожки работают всегда")
    T.switch(opt, 3, "Сначала только транскрипт", ctk.BooleanVar(value=False),
             "правлю текст вручную, потом собираю рыбу")
    T.device_row(opt, 5)

    row = ctk.CTkFrame(wrap, fg_color="transparent")
    row.pack(fill="x", padx=PAD, pady=(0, PAD))
    T.btn(row, "Собрать рыбу", "primary", width=180).pack(side="left")
    T.btn(row, "Собрать из правленого текста", "ghost").pack(side="left",
                                                            padx=10)
    return wrap


def page_grid(parent):
    """Витрина сетки превью — как выглядит страница «Просмотр»."""
    wrap = ctk.CTkScrollableFrame(parent, fg_color="transparent")
    head = ctk.CTkFrame(wrap, fg_color="transparent")
    head.pack(fill="x", padx=PAD, pady=(0, 12))
    ctk.CTkLabel(head, text="Рыба, 73 куска · 6:53", text_color=T.TEXT,
                 anchor="w", font=T.font(14, "bold")).pack(anchor="w")
    T.btn(head, "Собрать превью и открыть", "primary").pack(anchor="e")

    grid = ctk.CTkFrame(wrap, fg_color="transparent")
    grid.pack(fill="x", padx=PAD)
    for c in range(4):
        grid.grid_columnconfigure(c, weight=1, uniform="t")
    shots = [("00:02:14", "ВЕДУЩИЙ", "привет! У меня сегодня в гостях…"),
             ("00:02:20", "ГОСТЬ", "Привет, Катя."),
             ("00:02:26", "ВЕДУЩИЙ", "Расскажи вкратце о правилах…"),
             ("00:02:38", "ГОСТЬ", "ДНД, подземелья и драконы."),
             ("00:02:52", "ВЕДУЩИЙ", "а как тебе такой жанр?"),
             ("00:03:10", "ГОСТЬ", "мне нравится, что тут есть выбор…"),
             ("00:03:34", "ГОСТЬ", "собираю партию и всегда забываю…"),
             ("00:04:02", "ВЕДУЩИЙ", "а ты сам играл за мастера?")]
    for k, (t, who, text) in enumerate(shots):
        cell = ctk.CTkFrame(grid, fg_color=T.SURFACE, corner_radius=8)
        cell.grid(row=k // 4, column=k % 4, sticky="nsew", padx=4, pady=4)
        ctk.CTkLabel(cell, text="", height=92, corner_radius=6,
                     fg_color=T.RAISED).pack(fill="x", padx=8, pady=(8, 4))
        ctk.CTkLabel(cell, text=f"{who} · {t}", text_color=T.ACCENT,
                     anchor="w", font=T.font(11, "bold")).pack(anchor="w",
                                                              padx=8)
        ctk.CTkLabel(cell, text=text, text_color=T.MUTED, anchor="w",
                     font=T.font(11), wraplength=150, justify="left").pack(
                         anchor="w", padx=8, pady=(0, 8))
    return wrap


def page_sliders(parent):
    wrap = ctk.CTkScrollableFrame(parent, fg_color="transparent")
    cut = T.card(wrap, "Резка речи", "Пороги из settings.json")
    T.slider(cut, 0, "Пауза до разрыва, с", ctk.DoubleVar(value=0.60),
             0.1, 3.0, " с")
    T.slider(cut, 1, "Склеивать разрывы до, с", ctk.DoubleVar(value=0.80),
             0.1, 3.0, " с")
    T.slider(cut, 2, "Минимальный кусок, с", ctk.DoubleVar(value=1.00),
             0.2, 5.0, " с")
    T.slider(cut, 3, "Порог уверенности слов", ctk.DoubleVar(value=0.25),
             0.05, 0.80)

    shot = T.card(wrap, "Камера")
    T.slider(shot, 0, "Короткий кадр, с", ctk.DoubleVar(value=2.0),
             1.0, 6.0, " с")
    T.slider(shot, 1, "Длинный кадр, с", ctk.DoubleVar(value=12.0),
             5.0, 30.0, " с")
    T.slider(shot, 2, "Доля основной камеры", ctk.DoubleVar(value=0.70),
             0.1, 1.0)

    sw = T.card(wrap, "Поведение")
    T.switch(sw, 0, "Отсекать куски без картинки", ctk.BooleanVar(value=True),
             "камера не включена — выкидываем молчание вместо речи")
    T.switch(sw, 2, "Прикрывать стыки перебивками", ctk.BooleanVar(value=False))

    T.btn(wrap, "Сохранить настройки", "primary", width=200).pack(
        anchor="w", padx=PAD, pady=(0, PAD))
    return wrap


def page_stub(parent):
    wrap = ctk.CTkFrame(parent, fg_color="transparent")
    T.card(wrap, "Будущий шаблон", "Сюда, например, встанет телешоу")
    T.btn(wrap, "Собрать", "primary", width=180).pack(anchor="w", padx=PAD,
                                                     pady=(0, PAD))
    return wrap


class Demo(ctk.CTk):
    PAGES = [("Интервью 2 камеры", page_form),
             ("Просмотр", page_grid),
             ("Настройки", page_sliders),
             ("Заглушка", page_stub)]

    def __init__(self):
        super().__init__()
        self.title("AutoCut — демо оформления")
        self.geometry("940x700")
        self.configure(fg_color=T.BG)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._nav = {}

        side = ctk.CTkFrame(self, fg_color=T.SURFACE, corner_radius=0,
                            width=232)
        side.grid(row=0, column=0, sticky="ns")
        side.grid_propagate(False)
        ctk.CTkLabel(side, text="AutoCut", text_color=T.TEXT, anchor="w",
                     font=T.font(21, "bold")).pack(anchor="w", padx=PAD,
                                                   pady=(24, 2))
        ctk.CTkLabel(side, text="демо оформления", text_color=T.MUTED,
                     anchor="w", font=T.font(11)).pack(anchor="w", padx=PAD,
                                                      pady=(0, 20))
        for name, _ in self.PAGES:
            T.sidebar_item(side, name, command=lambda n=name: self.show(n)
                           ).pack(fill="x", padx=10, pady=1)
            self._nav[name] = side.winfo_children()[-1]

        body = ctk.CTkFrame(self, fg_color=T.BG, corner_radius=0)
        body.grid(row=0, column=1, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(2, weight=1)

        self.title_lbl = ctk.CTkLabel(body, text="", text_color=T.TEXT,
                                      anchor="w", font=T.font(23, "bold"))
        self.title_lbl.grid(row=0, column=0, sticky="w", padx=PAD,
                            pady=(PAD + 6, 2))
        self.sub_lbl = ctk.CTkLabel(body, text="", text_color=T.MUTED,
                                    anchor="w", font=T.font(12))
        self.sub_lbl.grid(row=1, column=0, sticky="w", padx=PAD, pady=(0, 8))
        self.host = ctk.CTkFrame(body, fg_color="transparent")
        self.host.grid(row=2, column=0, sticky="nsew")
        self.host.grid_columnconfigure(0, weight=1)
        self.host.grid_rowconfigure(0, weight=1)
        self.show("Интервью 2 камеры")

    def show(self, name):
        for w in self.host.winfo_children():
            w.destroy()
        self.title_lbl.configure(text=name)
        self.sub_lbl.configure(text=SUBS.get(name, ""))
        for n, b in self._nav.items():
            b.configure(fg_color=T.RAISED if n == name else "transparent",
                        text_color=T.TEXT if n == name else T.MUTED)
        # страницу ищем по имени, а не по переменной цикла: после цикла
        # остаётся последняя, и открывается всегда одна и та же
        page = next(f for n, f in self.PAGES if n == name)
        page(self.host).grid(row=0, column=0, sticky="nsew")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    app = Demo()
    if "--selftest" in argv:
        for name, _ in Demo.PAGES:
            app.show(name)
            app.update_idletasks()
        app.destroy()
        print("DEMO OK: все страницы собрались")
        return 0
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
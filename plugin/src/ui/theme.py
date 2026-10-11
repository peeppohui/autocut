# -*- coding: utf-8 -*-
"""Оформление AutoCut: палитра и готовые элементы.

Отдельный модуль, потому что оформление нужно и рабочему окну
(app.py), и демке (demo_ctk.py). Держать его в двух местах - значит
через месяц они разъедутся, и полк��ны окна будут разного цвета.

Правило простое: любой новый элемент берётся отсюда, а не через
голый ctk. Тогда окно не рассыпается, когда дописываешь поля.
"""
from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

# ---------------------------------------------------------------- палитра
# Дефолтная синяя тема customtkinter выглядит как чужой инструмент.
# Свой набор цветов, чтобы окно выглядело частью проекта.
BG = "#0f1115"          # фон окна
SURFACE = "#171a21"     # карточка
RAISED = "#1e222b"      # поле, приподнятое
BORDER = "#2a2f3a"
TEXT = "#e6e9ef"
MUTED = "#8b93a3"       # подписи, подсказки
FAINT = "#5f6779"       # заголовки групп
ACCENT = "#5b8cff"      # главное действие
ACCENT_DIM = "#2f4a8f"
SUCCESS = "#3ecf8e"
DANGER = "#f2555a"

R = 10                  # единый радиус скругления
PAD = 20                # единый отступ

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def font(size=13, weight="normal"):
    return ctk.CTkFont(size=size, weight=weight)


def mono(size=12):
    return ctk.CTkFont(family="Consolas", size=size)


# ---------------------------------------------------------------- кнопки
def btn(parent, text, kind="ghost", **kw):
    """Кнопки одного семейства: главная, второстепенная, тихая."""
    looks = {
        "primary": dict(fg_color=ACCENT, hover_color="#7aa2ff",
                        text_color="#0b1020", height=42, corner_radius=R),
        "ghost": dict(fg_color=RAISED, hover_color=BORDER,
                      text_color=TEXT, height=36, corner_radius=8),
        "quiet": dict(fg_color="transparent", hover_color=RAISED,
                      text_color=MUTED, height=30, corner_radius=8),
        "danger": dict(fg_color="transparent", hover_color="#3a1f22",
                       text_color=DANGER, height=36, corner_radius=8),
    }
    return ctk.CTkButton(parent, text=text, **looks[kind], **kw)


# ---------------------------------------------------------------- блоки
def card(parent, title, subtitle=""):
    """Карточка с заголовком. Возвращает внутренний фрейм для полей."""
    box = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=R)
    # box обязан получить геометрию сам: если этого не сделать, он остаётся
    # 1x1, и всё содержимое карточки схлопывается вместе с ним
    box.pack(fill="x", padx=PAD, pady=(0, 16))
    head = ctk.CTkFrame(box, fg_color="transparent")
    head.pack(fill="x", padx=PAD, pady=(PAD, 0))
    ctk.CTkLabel(head, text=title, text_color=TEXT, anchor="w",
                 font=font(15, "bold")).pack(anchor="w")
    if subtitle:
        ctk.CTkLabel(head, text=subtitle, text_color=MUTED, anchor="w",
                     font=font(12)).pack(anchor="w", pady=(2, 0))
    body = ctk.CTkFrame(box, fg_color="transparent")
    # внутри прокрутки expand=True схлопывает карточку в 1x1: высоту
    # прокручиваемая рамка берёт сама, по содержимому
    body.pack(fill="x", padx=PAD, pady=(14, PAD))
    body.grid_columnconfigure(1, weight=1)
    return body


def field(body, row, label, var, placeholder="", browse=None, width=None):
    """Строка «подпись — поле — кнопка». Возвращает переменную."""
    ctk.CTkLabel(body, text=label, text_color=MUTED, anchor="w",
                 width=width or 180, font=font(13)).grid(
                     row=row, column=0, sticky="w", pady=5, padx=(0, 14))
    ctk.CTkEntry(body, textvariable=var, height=36, corner_radius=8,
                 fg_color=RAISED, border_color=BORDER, border_width=1,
                 text_color=TEXT, font=font(13),
                 placeholder_text=placeholder).grid(
                     row=row, column=1, sticky="ew", pady=5)
    if browse is not None:
        btn(body, "…", "ghost", width=36, command=browse).grid(
            row=row, column=2, padx=(8, 0))
    return var


def switch(body, row, label, var, hint=""):
    ctk.CTkSwitch(body, text=label, variable=var, progress_color=ACCENT,
                  button_color=TEXT, button_hover_color="#ffffff",
                  text_color=TEXT, font=font(13),
                  width=46, height=24).grid(row=row, column=0, columnspan=2,
                                            sticky="w", pady=6)
    if hint:
        ctk.CTkLabel(body, text=hint, text_color=MUTED, anchor="w",
                     font=font(11)).grid(row=row + 1, column=0, columnspan=3,
                                          sticky="w")
    return var


def slider(body, row, label, var, lo, hi, suffix=""):
    """Ползунок с текущим значением справа - видно, куда упёрлось."""
    ctk.CTkLabel(body, text=label, text_color=MUTED, anchor="w",
                 font=font(13)).grid(row=row, column=0, sticky="w")
    val = ctk.CTkLabel(body, text="", text_color=TEXT, anchor="e", width=62,
                       font=font(13, "bold"))

    def show(*_):
        v = var.get()
        val.configure(text=f"{v:.2f}".rstrip("0").rstrip(".") + suffix)

    var.trace_add("write", show)
    show()
    ctk.CTkSlider(body, from_=lo, to=hi, number_of_steps=100,
                  variable=var, height=14, button_color=ACCENT,
                  button_hover_color="#7aa2ff", progress_color=ACCENT_DIM,
                  fg_color=BORDER).grid(row=row, column=1, sticky="ew",
                                        padx=14, pady=6)
    val.grid(row=row, column=2, sticky="e")
    return var


def device_row(body, row, label="Устройство:"):
    """Переключатель CPU/GPU. Авто = GPU если завелась, иначе тихо CPU."""
    var = ctk.StringVar(value="auto")
    ctk.CTkLabel(body, text=label, text_color=MUTED, anchor="w",
                 width=180, font=font(13)).grid(row=row, column=0, sticky="w",
                                                pady=5, padx=(0, 14))
    ctk.CTkOptionMenu(body, variable=var, values=["auto", "cpu", "cuda"],
                      height=36, corner_radius=8, fg_color=RAISED,
                      button_color=BORDER, button_hover_color="#353b48",
                      text_color=TEXT, font=font(13)).grid(
                          row=row, column=1, sticky="w", pady=5)
    return var


def logbox(parent):
    """Поле лога. state='disabled' - чтобы пользователь не мешал выводу."""
    box = ctk.CTkTextbox(parent, fg_color=SURFACE, corner_radius=R,
                         border_color=BORDER, border_width=1,
                         text_color=MUTED, font=mono(12))
    return box


def sidebar_item(side, text, command, active=False):
    return ctk.CTkButton(side, text=text, anchor="w", height=36,
                         corner_radius=8,
                         fg_color=RAISED if active else "transparent",
                         hover_color=RAISED,
                         text_color=TEXT if active else MUTED,
                         font=font(13), command=command)


def group_label(side, text):
    return ctk.CTkLabel(side, text=text.upper(), text_color=FAINT, anchor="w",
                        font=font(10, "bold"))
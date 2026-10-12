from __future__ import annotations

import sys
from pathlib import Path

import customtkinter as ctk

HERE = Path(__file__).resolve().parent        # plugin/src/ui
SRC = HERE.parent                            # plugin/src
ML = SRC / "ml"

sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "config"))

import theme as T                            # noqa: E402


def multicam_card(parent, app, proj_var):
    """Подготовка камер к настоящему мультикаму Premiere.

    Живёт в «Интервью 2 камеры», а не отдельным пунктом меню: мультикам
    - это второй способ разложить две камеры, и он имеет смысл только
    там, где камеры работают одновременно. Отдельный блок про это
    приходилось бы искать, когда он уже не нужен.

    Синхронизацию делает всё то же интервью (шаг 1 «Собрать рыбы»),
    поэтому карточка берёт папку synced из папки проекта.
    """
    dst = T.card(parent, "Мультикам в Premiere",
                 "Второй способ разложить две камеры. Если тебе хватает "
                 "переключения глазком по второй дорожке — этот блок не нужен")
    folder = ctk.StringVar()
    T.field(dst, 0, "Папка synced", folder, r"нажми «Подставить»",
            browse=lambda: app.pick_dir(folder, "Папка synced"))
    tc = T.field(dst, 1, "Начало таймкода",
                 ctk.StringVar(value="01:00:00:00"),
                 "одинаковый для всех камер")
    note = ctk.CTkLabel(dst, text="", text_color=T.MUTED, anchor="w",
                        font=T.font(11), wraplength=620, justify="left")
    note.grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

    how = T.card(parent, "Что произойдёт",
                 "Мультикам строится самим Premiere, вручную")
    # многострочные подписи - через pack: в grid строки с текстом
    # разной высоты наезжают друг на друга
    for text in (
        "1. Каждой камере делается копия с одинаковым таймкодом.\n"
        "   Копия нужна, чтобы не трогать оригиналы; видео при этом\n"
        "   НЕ перекодируется - только метка, это быстро.",
        "2. Папку с копиями импортируешь в Premiere, выделяешь все\n"
        "   файлы и жмёшь: ПКМ > Multi-Camera > Create Multicam Sequence.",
        "3. В окне выбираешь Synchronize Point: Timecode. Всё, ракурсы\n"
        "   встанут друг на друга сами.",
    ):
        ctk.CTkLabel(how, text=text, text_color=T.MUTED, anchor="w",
                     justify="left", font=T.font(12)).pack(anchor="w",
                                                          pady=4)

    def synced_dir():
        return Path(proj_var.get().strip().strip('"')) / "synced"

    def autofill():
        f = synced_dir()
        if not f.is_dir():
            note.configure(text="Папки synced пока нет. Собери рыбу выше — "
                                "синхронизация идёт её первым шагом.",
                           text_color=T.MUTED)
            return False
        n = len([p for p in f.glob("*")
                 if p.suffix.lower() in (".mp4", ".mov", ".mxf", ".mkv")])
        folder.set(str(f))
        note.configure(text=f"Готово: камер в папке — {n}",
                       text_color=T.SUCCESS if n else T.MUTED)
        return n > 0

    def run():
        f = Path(folder.get().strip().strip('"'))
        if not f.is_dir():
            app.warn("Папка synced не найдена. Нажми «Подставить» или "
                     "собери рыбу.")
            return
        vids = sorted(p for p in f.glob("*")
                      if p.suffix.lower() in (".mp4", ".mov", ".mxf", ".mkv"))
        if not vids:
            app.warn("В папке нет видео: синхронизация ещё не отработала.")
            return
        out = f.parent / "multicam"
        app.go([("Готовлю камеры к мультикаму",
                 [str(ML / "multicam.py"),
                  "--input", *[str(v) for v in vids],
                  "--outdir", str(out),
                  "--timecode", tc.get().strip() or "01:00:00:00"])],
               str(out))

    row = ctk.CTkFrame(dst, fg_color="transparent")
    row.grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))
    T.btn(row, "Подготовить камеры", "primary", width=220,
          command=run).pack(side="left")
    T.btn(row, "Подставить", "ghost", width=150,
          command=autofill).pack(side="left", padx=10)
    autofill()
    return note
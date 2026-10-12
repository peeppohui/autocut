#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoCut — окно сбора рыбы. Терминал больше не нужен.
Запуск: двойной клик по AutoCut.bat (или python app.py).

Слева список шаблонов: сначала те, что нужны каждый день, потом
остальные. Логика запуска шагов и отмены не менялась с прошлой
версии на ttk — перенесено только оформление.

Все элементы берутся из ui/theme.py. Если добавляешь поле — бери
field/card/switch оттуда, иначе окно начнёт выглядеть собранным
из двух разных программ.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

HERE = Path(__file__).resolve().parent        # plugin/src/ui
SRC = HERE.parent                            # plugin/src
BASE = SRC.parents[1]                        # корень проекта — рабочий cwd
PY = sys.executable

ML = SRC / "ml"                              # detect, sync, music
MODES = SRC / "modes"                        # autocut, interview, review
IO = SRC / "io"                              # premiere, preview

sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "config"))

# Модульные папки (core, modes, io, ml) лежат рядом и по имени не видны
# друг другу. Без этого страница «Просмотр» падала с
# «No module named 'preview» — кнопка «Показать» молча ничего не делала.
import bootstrap  # noqa: E402,F401

import theme as T                            # noqa: E402
import settings                              # noqa: E402
import pages_news                            # noqa: E402
import panel_result                          # noqa: E402


# ==================================================================
#  запуск шагов — этот код без изменений с прошлой версии
# ==================================================================
def run_steps(steps, log, done, state):
    """steps: [(заголовок, [аргументы])]. Стоп на первой ошибке или по отмене."""
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    for title, argv in steps:
        if state.get("cancel"):
            log("ОТМЕНЕНО пользователем.")
            done(False)
            return
        log(f"\n=== {title} ===")
        try:
            p = subprocess.Popen(
                [PY, "-u"] + argv, cwd=str(BASE),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                env=env)
            state["proc"] = p
        except Exception as e:
            log(f"ОШИБКА запуска: {e}")
            done(False)
            return
        for line in p.stdout:
            log(line.rstrip())
            if state.get("cancel"):
                kill_tree(p)
                log("ОТМЕНЕНО пользователем.")
                state["proc"] = None
                done(False)
                return
        p.wait()
        state["proc"] = None
        if p.returncode != 0:
            log(f"ОШИБКА на шаге «{title}» (код {p.returncode}). Дальше не иду.")
            done(False)
            return
    done(True)


def kill_tree(p):
    """Убить процесс ВМЕСТЕ с детьми (ffmpeg), иначе они висят в фоне."""
    try:
        p.terminate()
    except Exception:
        pass
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                       capture_output=True, timeout=15)
    except Exception:
        pass


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("AutoCut")
        self.geometry("1040x780")
        self.minsize(920, 640)
        self.configure(fg_color=T.BG)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.q = queue.Queue()
        self.busy = False
        # не self.state: так называется метод Tk, и словарь его затирал
        self.run_state = {"proc": None, "cancel": False}
        self.result_dir = None
        # просят две кнопки на странице интервью, держим одну переменную
        self.transcript_path = ctk.StringVar()
        self._nav = {}
        # панели разбора монтажа на текущей странице. После сборки они
        # должны сами подхватить новый результат, иначе пришлось бы
        # жать «Показать» руками
        self.panels = []

        # ---- левая панель ----
        side = ctk.CTkFrame(self, fg_color=T.SURFACE, corner_radius=0,
                            width=236)
        side.grid(row=0, column=0, sticky="ns")
        side.grid_propagate(False)
        # логотип; если файла нет — просто текст, это не поломка
        pic = T.logo(140)
        if pic is not None:
            ctk.CTkLabel(side, text="", image=pic).pack(
                anchor="w", padx=(T.PAD - 8, 0), pady=(20, 14))
        else:
            ctk.CTkLabel(side, text="AutoCut", text_color=T.TEXT, anchor="w",
                         font=T.font(21, "bold")).pack(anchor="w", padx=T.PAD,
                                                       pady=(24, 22))
        T.apply_icon(self)
        for gname, items in GROUPS:
            T.group_label(side, gname).pack(anchor="w", padx=T.PAD,
                                            pady=(8, 4))
            for name, fn in items:
                T.sidebar_item(side, name,
                               command=lambda n=name: self.show(n)
                               ).pack(fill="x", padx=10, pady=1)
                self._nav[name] = side.winfo_children()[-1]

        # ---- правая часть: заголовок, страница, лог ----
        body = ctk.CTkFrame(self, fg_color=T.BG, corner_radius=0)
        body.grid(row=0, column=1, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        # вес у страницы, а не у лога: иначе лог растягивается на пол-окна
        # и поля не влезают
        body.grid_rowconfigure(2, weight=1)

        self.title_lbl = ctk.CTkLabel(body, text="", text_color=T.TEXT,
                                      anchor="w",
                                      font=T.font(22, "bold"))
        self.title_lbl.grid(row=0, column=0, sticky="w", padx=T.PAD,
                            pady=(T.PAD + 6, 2))
        self.sub_lbl = ctk.CTkLabel(body, text="", text_color=T.MUTED,
                                    anchor="w", font=T.font(12))
        self.sub_lbl.grid(row=1, column=0, sticky="w", padx=T.PAD, pady=(0, 10))

        self.host = ctk.CTkFrame(body, fg_color="transparent")
        self.host.grid(row=2, column=0, sticky="nsew")
        self.host.grid_columnconfigure(0, weight=1)
        self.host.grid_rowconfigure(0, weight=1)

        self.logw = T.logbox(body)
        # лог фиксированной высоты и всегда виден: он нужен, пока идёт
        # сборка, а страница должна дышать
        self.logw.configure(height=150)
        self.logw.grid(row=3, column=0, sticky="ew",
                       padx=T.PAD, pady=(12, 0))

        bar = ctk.CTkFrame(body, fg_color="transparent")
        bar.grid(row=4, column=0, sticky="ew", padx=T.PAD, pady=12)
        self.cancel_btn = T.btn(bar, "Остановить", "danger", width=150,
                                command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="left")
        self.open_btn = T.btn(bar, "Открыть папку результата", "ghost",
                              width=230, command=self.open_result,
                              state="disabled")
        self.open_btn.pack(side="right")

        self.show("Интервью 2 камеры")
        self.after(120, self.pump)

    # ---------- навигация ----------
    def show(self, name):
        for w in self.host.winfo_children():
            w.destroy()
        # страница пересоздаётся, её панели больше не существуют
        self.panels.clear()
        self.title_lbl.configure(text=name)
        self.sub_lbl.configure(text=SUBS.get(name, ""))
        for n, b in self._nav.items():
            b.configure(fg_color=T.RAISED if n == name else "transparent",
                        text_color=T.TEXT if n == name else T.MUTED)
        page = next(f for n, f in TPL if n == name)
        page(self.host, self).grid(row=0, column=0, sticky="nsew")

    # ---------- лог и поток ----------
    def pump(self):
        try:
            while True:
                line = self.q.get_nowait()
                self.logw.configure(state="normal")
                self.logw.insert("end", line + "\n")
                self.logw.see("end")
                self.logw.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(120, self.pump)

    def log(self, s):
        self.q.put(s)

    # ---------- общее для всех страниц ----------
    def pick_dir(self, var, title):
        v = filedialog.askdirectory(title=title)
        if v:
            var.set(v)

    def pick_file(self, var, title, types=None):
        v = filedialog.askopenfilename(title=title, filetypes=types or
                                       [("Все файлы", "*.*")])
        if v:
            var.set(v)

    def go(self, steps, result_dir):
        if self.busy:
            return
        self.busy = True
        self.run_state = {"proc": None, "cancel": False}
        self.result_dir = result_dir
        self.open_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        threading.Thread(target=run_steps,
                         args=(steps, self.log, self.finished,
                               self.run_state),
                         daemon=True).start()

    def cancel(self):
        if not self.busy:
            return
        self.log("Отмена... останавливаю текущий шаг (добиваю ffmpeg).")
        self.run_state["cancel"] = True
        p = self.run_state.get("proc")
        if p is not None:
            kill_tree(p)

    def finished(self, ok):
        def _ui():
            self.busy = False
            self.cancel_btn.configure(state="disabled")
            self.log("ГОТОВО." if ok else "ОСТАНОВЛЕНО С ОШИБКОЙ — читай лог выше.")
            if ok and self.result_dir and Path(self.result_dir).exists():
                self.open_btn.configure(state="normal")
                # монтаж только что пересобрался - разбор должен показать
                # новую раскладку сам, без лишнего клика
                if self.result_dir:
                    for p in list(self.panels):
                        try:
                            p.dir_var.set(str(self.result_dir))
                        except Exception:
                            pass
                self.refresh_panels()
            if ok:
                try:
                    self.bell()
                except Exception:
                    pass
        self.after(0, _ui)

    def open_result(self):
        if self.result_dir:
            try:
                os.startfile(self.result_dir)
            except Exception as e:
                messagebox.showinfo("AutoCut", str(e))

    def need(self, var, what):
        v = var.get().strip().strip('"')
        if not v or not Path(v).exists():
            messagebox.showwarning("AutoCut", f"Укажи существующий путь: {what}")
            return None
        return v

    def warn(self, text):
        """Предупреждение пользователю.

        Отдельный метод, а не прямой вызов messagebox, потому что тесты
        подменяют его одним заглушечным объектом. Иначе пришлось бы
        латать каждый новый вызов в каждой странице.
        """
        messagebox.showwarning("AutoCut", text)

    def register_panel(self, panel):
        """Панель разбора монтажа сама переживёт следующую сборку."""
        self.panels.append(panel)

    def refresh_panels(self):
        for p in list(self.panels):
            try:
                p.load()
            except Exception as e:
                # панель - украшение, ронять из-за неё сборку нельзя
                self.log(f"Разбор монтажа не обновился: {type(e).__name__}: {e}")


# ==================================================================
#  страницы
# ==================================================================
def page_multi(parent, app):
    """Интервью 2 камеры — основной рабочий режим."""
    wrap = ctk.CTkScrollableFrame(parent, fg_color="transparent")

    src = T.card(wrap, "Исходники", "Синхронизируются между собой по звуку")
    wide = T.field(src, 0, "Общий план", ctk.StringVar(), "camera_wide.mov",
                   browse=lambda: app.pick_file(wide, "Общий план"))
    gcam = T.field(src, 1, "Камера гостя", ctk.StringVar(), "camera_guest.mov",
                   browse=lambda: app.pick_file(gcam, "Камера гостя"))
    gmic = T.field(src, 2, "Микрофон гостя", ctk.StringVar(), "_guest.wav",
                   browse=lambda: app.pick_file(gmic, "Микрофон гостя"))
    hmic = T.field(src, 3, "Микрофон ведущего", ctk.StringVar(), "_host.wav",
                   browse=lambda: app.pick_file(hmic, "Микрофон ведущего"))

    opt = T.card(wrap, "Монтаж")
    tgt = ctk.StringVar()
    T.field(opt, 0, "Хронометраж (ММ:СС)", tgt, "пусто — не ограничивать")
    two_cam = T.switch(opt, 1, "Второй ракурс на отдельной дорожке",
                       ctk.BooleanVar(value=True),
                       "Premiere не понимает multiclip — две дорожки работают "
                       "всегда, переключение потом кликом по глазку")
    only_tx = T.switch(opt, 3, "Сначала только транскрипт",
                       ctk.BooleanVar(value=False),
                       "правлю текст вручную, потом жму вторую кнопку")
    dev = T.device_row(opt, 5)

    dst = T.card(wrap, "Результат")
    out = T.field(dst, 0, "Папка проекта", ctk.StringVar(),
                  browse=lambda: app.pick_dir(out, "Куда сложить"))
    T.field(dst, 1, "Правленый transcript.txt", app.transcript_path,
            "нужно только для второй кнопки",
            browse=lambda: app.pick_file(app.transcript_path,
                                         "Правленый transcript.txt",
                                         [("Текст", "*.txt")]))

    def tgt_args():
        v = tgt.get().strip()
        return ["--target", v] if v else []

    def run_from_tx():
        t = app.transcript_path.get().strip().strip('"')
        if not t or not Path(t).is_file():
            messagebox.showwarning("AutoCut", "Укажи правленый transcript.txt")
            return
        w, g = app.need(wide, "общий план"), app.need(gcam, "камеру гостя")
        if not w or not g:
            return
        o = out.get().strip().strip('"') or str(BASE / "interview_out")
        Path(o).mkdir(parents=True, exist_ok=True)
        app.go([
            ("1. Сборка из правленого текста",
             [str(MODES / "interview.py"), "--wide", w, "--guest-cam", g,
              "--outdir", f"{o}/rz", "--from-transcript", t] + tgt_args()),
            ("2. XML для Premiere",
             [str(IO / "premiere.py"), "--timeline", f"{o}/rz/selects.csv",
              "--media-dir", o, "--out", f"{o}/ryba.xml",
              "--edl", f"{o}/ryba.edl", "--trim-rec"]
             + (["--stacked"] if two_cam.get() else [])),
            ("3. Субтитры",
             [str(IO / "srt.py"), "--selects", f"{o}/rz/selects.csv",
              "--out", f"{o}/ryba.srt"])], o)

    def run():
        paths = {}
        for var, what in ((wide, "общий план"), (gcam, "камеру гостя"),
                          (gmic, "микрофон гостя"),
                          (hmic, "микрофон ведущего")):
            v = app.need(var, what)
            if not v:
                return
            paths[what] = v
        o = out.get().strip().strip('"') or str(BASE / "interview_out")
        Path(o).mkdir(parents=True, exist_ok=True)
        step2 = ["--outdir", f"{o}/rz", "--device", dev.get()]
        if only_tx.get():
            step2 = ["--transcript-only"] + step2
        steps = [
            ("1. Синхронизация", [str(ML / "sync.py"), "--ref", paths["общий план"],
                                  "--cam", paths["камеру гостя"],
                                  "--mic-guest", paths["микрофон гостя"],
                                  "--mic-host", paths["микрофон ведущего"],
                                  "--outdir", f"{o}/synced"]),
            ("2. Рыба по тексту", [str(MODES / "interview.py"),
                                   "--wide", f"{o}/synced/ref.mp4",
                                   "--guest-cam", f"{o}/synced/cam.mp4",
                                   "--guest-mic", f"{o}/synced/mic_guest.wav",
                                   "--host-mic", f"{o}/synced/mic_host.wav"]
             + tgt_args() + step2),
        ]
        if not only_tx.get():
            steps.append(
                ("3. XML для Premiere",
                 [str(IO / "premiere.py"), "--timeline", f"{o}/rz/selects.csv",
                  "--media-dir", o, "--out", f"{o}/ryba.xml",
                  "--edl", f"{o}/ryba.edl", "--trim-rec"]
                 + (["--stacked"] if two_cam.get() else [])))
            steps.append(("4. Субтитры",
                          [str(IO / "srt.py"), "--selects", f"{o}/rz/selects.csv",
                           "--out", f"{o}/ryba.srt"]))
        else:
            app.log("Режим транскрипта: после шага 2 вычеркни мусор в "
                    "rz/transcript.txt строками с '-' и вернись сюда.")
        app.go(steps, o)

    row = ctk.CTkFrame(wrap, fg_color="transparent")
    row.pack(fill="x", padx=T.PAD, pady=(0, T.PAD))
    T.btn(row, "Собрать рыбу", "primary", width=180, command=run).pack(
        side="left")
    T.btn(row, "Собрать из правленого текста", "ghost",
          command=run_from_tx).pack(side="left", padx=10)
    panel_result.add_to(wrap, app, out, two_cam)
    return wrap


def page_mc(parent, app):
    """Подготовка камер к настоящему мультикаму Premiere.

    Premiere умеет строить мультикам сам, если у файлов совпадает
    таймкод. Всё, что делаем мы, - впечатываем метку: видео при
    этом не перекодируется, файлы просто копируются с таймкодом.

    Блок самодостаточный: синхронизация камер живёт здесь же. Раньше
    страница просила «готовые синхроны из папки synced», а взять их
    было только в другом блоке - то есть работать здесь было нельзя.
    """
    wrap = ctk.CTkScrollableFrame(parent, fg_color="transparent")

    dst = T.card(wrap, "Куда сложить", "Синхронизация и копии лягут сюда")
    proj = ctk.StringVar(value=str(BASE / "multicam_out"))
    T.field(dst, 0, "Папка проекта", proj,
            browse=lambda: app.pick_dir(proj, "Куда сложить"))

    # ------------------------------------------- 1. синхронизация
    prep = T.card(wrap, "1. Синхронизировать камеры",
                  "Свои файлы. Хлопок не нужен - камеры встают по звуку")
    wide = ctk.StringVar()
    cam2 = ctk.StringVar()
    mic1 = ctk.StringVar()
    mic2 = ctk.StringVar()
    T.field(prep, 0, "Общий план", wide, "камера 1, со звуком",
            browse=lambda: app.pick_file(wide, "Общий план"))
    T.field(prep, 1, "Камера 2", cam2, "вторая камера, если есть",
            browse=lambda: app.pick_file(cam2, "Камера 2"))
    T.field(prep, 2, "Микрофон 1", mic1, "*.wav",
            browse=lambda: app.pick_file(mic1, "Микрофон 1",
                                         [("Звук", "*.wav *.mp3")]))
    T.field(prep, 3, "Микрофон 2", mic2, "необязательно",
            browse=lambda: app.pick_file(mic2, "Микрофон 2",
                                         [("Звук", "*.wav *.mp3")]))
    dev = T.device_row(prep, 4)
    prep_note = ctk.CTkLabel(prep, text="", text_color=T.MUTED, anchor="w",
                             font=T.font(11), wraplength=620,
                             justify="left")
    prep_note.grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))

    # ------------------------------------------- 2. камеры
    src = T.card(wrap, "2. Камеры для мультикама",
                 "Подставляется из папки проекта после синхронизации")
    folder = ctk.StringVar()
    T.field(src, 0, "Папка synced", folder,
            r"нажми «Подставить»",
            browse=lambda: app.pick_dir(folder, "Папка synced"))
    tc = T.field(src, 1, "Начало таймкода", ctk.StringVar(value="01:00:00:00"),
                 "одинаковый для всех камер")

    how = T.card(wrap, "Что произойдёт",
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
                     justify="left", font=T.font(12)).pack(
                         anchor="w", pady=4)

    def synced_dir():
        return Path(proj.get().strip().strip('"')
                    or str(BASE / "multicam_out")) / "synced"

    def autofill():
        f = synced_dir()
        if not f.is_dir():
            prep_note.configure(text="Папки synced пока нет. "
                                    "Заполни камеры и нажми «Синхронизировать».",
                                text_color=T.MUTED)
            return False
        n = len([p for p in f.glob("*")
                 if p.suffix.lower() in (".mp4", ".mov", ".mxf", ".mkv")])
        folder.set(str(f))
        prep_note.configure(text=f"Готово: {n} камер в папке проекта",
                            text_color=T.SUCCESS if n else T.MUTED)
        return n > 0

    def do_sync():
        w = app.need(wide, "общий план")
        if not w:
            return
        argv = [str(ML / "sync.py"), "--ref", w, "--outdir", str(synced_dir())]
        c = cam2.get().strip().strip('"')
        if c and Path(c).is_file():
            argv += ["--cam", c]
        for mic in (mic1, mic2):
            m = mic.get().strip().strip('"')
            if m and Path(m).is_file():
                argv += ["--mic-guest" if mic is mic1 else "--mic-host", m]
        app.go([("Синхронизация", argv)], str(synced_dir()))

    def run():
        f = Path(folder.get().strip().strip('"'))
        if not f.is_dir():
            app.warn("Папка synced не найдена. Нажми «Подставить» "
                     "или «Синхронизировать».")
            return
        vids = sorted(p for p in f.glob("*")
                      if p.suffix.lower() in (".mp4", ".mov", ".mxf", ".mkv"))
        if not vids:
            app.warn("В папке нет видео. Сначала нажми «Синхронизировать».")
            return
        out = f.parent / "multicam"
        app.go([("Готовлю камеры к мультикаму",
                 [str(ML / "multicam.py"),
                  "--input", *[str(v) for v in vids],
                  "--outdir", str(out),
                  "--timecode", tc.get().strip() or "01:00:00:00"])],
               str(out))

    prow = ctk.CTkFrame(prep, fg_color="transparent")
    prow.grid(row=6, column=0, columnspan=3, sticky="w", pady=(4, 8))
    T.btn(prow, "Синхронизировать", "ghost", width=190,
          command=do_sync).pack(side="left")
    T.btn(prow, "Подставить", "primary", width=150,
          command=autofill).pack(side="left", padx=10)
    ctk.CTkLabel(prow, text="← берёт папку synced из папки проекта",
                 text_color=T.FAINT, font=T.font(11)).pack(side="left",
                                                           padx=10)

    T.btn(wrap, "Подготовить камеры", "primary", width=220,
          command=run).pack(anchor="w", padx=T.PAD, pady=(0, T.PAD))
    autofill()
    return wrap


def page_settings(parent, app):
    """Пороги из settings.json — правятся прямо здесь."""
    wrap = ctk.CTkScrollableFrame(parent, fg_color="transparent")

    cut = T.card(wrap, "Резка речи", "Пороги из settings.json")
    max_pause = T.slider(cut, 0, "Пауза до разрыва, с",
                         ctk.DoubleVar(value=float(
                             settings.get("interview.max_pause_sec", 0.6))),
                         0.1, 3.0, " с")
    merge_gap = T.slider(cut, 1, "Склеивать разрывы до, с",
                         ctk.DoubleVar(value=float(
                             settings.get("interview.merge_gap_sec", 0.8))),
                         0.1, 3.0, " с")
    min_keep = T.slider(cut, 2, "Минимальный кусок, с",
                        ctk.DoubleVar(value=float(
                            settings.get("interview.min_keep_sec", 1.0))),
                        0.2, 5.0, " с")
    min_prob = T.slider(cut, 3, "Порог уверенности слов",
                        ctk.DoubleVar(value=float(
                            settings.get("interview.min_word_prob", 0.25))),
                        0.05, 0.80)
    drop_black = T.switch(cut, 4, "Отсекать куски без картинки",
                          ctk.BooleanVar(value=bool(
                              settings.get("black.enabled", True))),
                          "камера не включена — выкидываем молчание вместо речи")

    shot = T.card(wrap, "Камера")
    shot_min = T.slider(shot, 0, "Короткий кадр, с",
                        ctk.DoubleVar(value=float(
                            settings.get("interview_cuts.shot_min_sec", 2.0))),
                        1.0, 6.0, " с")
    shot_max = T.slider(shot, 1, "Длинный кадр, с",
                        ctk.DoubleVar(value=float(
                            settings.get("interview_cuts.shot_max_sec", 12.0))),
                        5.0, 30.0, " с")
    share_a = T.slider(shot, 2, "Доля основной камеры",
                       ctk.DoubleVar(value=float(
                           settings.get("interview_cuts.cam_a_share", 0.7))),
                       0.1, 1.0)

    note = ctk.CTkLabel(wrap, text="", text_color=T.SUCCESS, anchor="w",
                        font=T.font(12))
    note.pack(anchor="w", padx=T.PAD, pady=(0, 10))

    def save():
        for key, var in (("interview.max_pause_sec", max_pause),
                         ("interview.merge_gap_sec", merge_gap),
                         ("interview.min_keep_sec", min_keep),
                         ("interview.min_word_prob", min_prob),
                         ("black.enabled", drop_black),
                         ("interview_cuts.shot_min_sec", shot_min),
                         ("interview_cuts.shot_max_sec", shot_max),
                         ("interview_cuts.cam_a_share", share_a)):
            v = var.get()
            settings.set_value(key, round(float(v), 3)
                               if isinstance(v, float) else v)
        note.configure(text=f"Сохранено в settings.json, {FIELDS} значений")
        note.after(4000, lambda: note.configure(text=""))

    T.btn(wrap, "Сохранить настройки", "primary", width=200,
          command=save).pack(anchor="w", padx=T.PAD, pady=(0, T.PAD))
    return wrap


def page_digest(parent, app):
    """Дайджест: папка исходников -> раскладка по планам."""
    wrap = ctk.CTkScrollableFrame(parent, fg_color="transparent")
    src = T.card(wrap, "Исходники")
    inp = T.field(src, 0, "Папка исходников", ctk.StringVar(),
                  browse=lambda: app.pick_dir(inp, "Папка исходников"))
    out = T.field(src, 1, "Куда сложить", ctk.StringVar(),
                  browse=lambda: app.pick_dir(out, "Куда сложить"))
    tgt = T.field(src, 2, "Хронометраж, сек", ctk.StringVar(value="60"))
    mus = T.field(src, 3, "Музыка (необязательно)", ctk.StringVar(),
                  browse=lambda: app.pick_file(mus, "Музыка"))
    dev = T.device_row(src, 4)

    def run():
        s = app.need(inp, "папку исходников")
        if not s:
            return
        o = out.get().strip().strip('"') or str(BASE / "digest_out")
        Path(o).mkdir(parents=True, exist_ok=True)
        t = tgt.get().strip() or "60"
        steps = [
            ("1. Определяю планы", [str(ML / "detect.py"), "--input", s,
                                    "--output", f"{o}/clips.csv", "--preview",
                                    f"{o}/preview", "--device", dev.get()]),
            ("2. Раскладываю по склейкам", [str(MODES / "autocut.py"),
                                            "--csv", f"{o}/clips.csv",
                                            "--mode", "digest", "--auto",
                                            "--finale-wide", "--target-sec", t,
                                            "--out", f"{o}/timeline.csv"]),
        ]
        tail = ["--out", f"{o}/ryba.xml", "--edl", f"{o}/ryba.edl",
                "--trim-rec"]
        if mus.get().strip():
            steps.append(("3. Подложка", [str(ML / "music.py"), "--music",
                                          mus.get().strip(), "--duration", t,
                                          "--out", f"{o}/bed.wav"]))
            steps.append(("4. XML для Premiere",
                          [str(IO / "premiere.py"), "--timeline",
                           f"{o}/timeline.csv", "--media-dir", s]
                          + tail + ["--music", f"{o}/bed.wav"]))
        else:
            steps.append(("3. XML для Premiere",
                          [str(IO / "premiere.py"), "--timeline",
                           f"{o}/timeline.csv", "--media-dir", s] + tail))
        app.log("Проверь preview после шага 1: где уверенность <= 0.65 — "
                "поправь CSV и жми снова.")
        app.go(steps, o)

    T.btn(wrap, "Собрать рыбу", "primary", width=180, command=run).pack(
        anchor="w", padx=T.PAD, pady=(0, T.PAD))
    panel_result.add_to(wrap, app, out)
    return wrap


def page_single(parent, app):
    """Одна камера, без второй.

    Поле «Перебивки CSV» раньше было объявлено, но никуда не передавалось:
    ты заполнял его впустую. Теперь оно подключено к interview.py --broll,
    а рядом стоит кнопка, которая сама делает этот CSV через detect.py.
    """
    wrap = ctk.CTkScrollableFrame(parent, fg_color="transparent")
    src = T.card(wrap, "Исходники")
    v = T.field(src, 0, "Видео интервью", ctk.StringVar(),
                browse=lambda: app.pick_file(v, "Видео интервью"))
    out = T.field(src, 1, "Куда сложить", ctk.StringVar(),
                  browse=lambda: app.pick_dir(out, "Куда сложить"))
    dev = T.device_row(src, 2)

    bro = T.card(wrap, "Перебивки", "Необязательно. Ими прикрывают стыки "
                 "монтажа. Нажми «Найти перебивки» — CSV сделается сам")
    bro_dir = ctk.StringVar()
    T.field(bro, 0, "Папка с перебивками", bro_dir,
            r"папка с короткими видео: зал, толпа, аппаратура",
            browse=lambda: app.pick_dir(bro_dir, "Папка с перебивками"))
    bro_csv = ctk.StringVar()
    T.field(bro, 1, "CSV перебивок", bro_csv,
            r"заполнится кнопкой ниже",
            browse=lambda: app.pick_file(bro_csv, "CSV перебивок",
                                         [("Таблица", "*.csv")]))
    bro_note = ctk.CTkLabel(bro, text="", text_color=T.MUTED, anchor="w",
                            font=T.font(11), wraplength=620, justify="left")
    bro_note.grid(row=2, column=0, columnspan=3, sticky="w")

    def project():
        return out.get().strip().strip('"') or str(BASE / "interview_out")

    def find_broll():
        d = bro_dir.get().strip().strip('"')
        if not d or not Path(d).is_dir():
            app.warn("Укажи папку с перебивками.")
            return
        o = project()
        Path(o).mkdir(parents=True, exist_ok=True)
        csv_out = Path(o) / "broll.csv"
        app.go([("Ищу перебивки",
                 [str(ML / "detect.py"), "--input", d,
                  "--output", str(csv_out),
                  "--preview", f"{o}/preview",
                  "--device", dev.get()])], o)

    def run():
        val = app.need(v, "видео интервью")
        if not val:
            return
        o = project()
        Path(o).mkdir(parents=True, exist_ok=True)
        argv = [str(MODES / "interview.py"), "--input", val,
                "--outdir", f"{o}/rz", "--device", dev.get()]
        b = bro_csv.get().strip().strip('"')
        if b and Path(b).is_file():
            argv += ["--broll", b]
        elif b:
            app.warn(f"CSV перебивок не найден, иду без прикрытия стыков: {b}")
        steps = [
            ("1. Рыба по тексту", argv),
            ("2. XML для Premiere",
             [str(IO / "premiere.py"), "--timeline", f"{o}/rz/selects.csv",
              "--media-dir", o, "--out", f"{o}/ryba.xml",
              "--edl", f"{o}/ryba.edl", "--trim-rec"]),
            ("3. Субтитры",
             [str(IO / "srt.py"), "--selects", f"{o}/rz/selects.csv",
              "--out", f"{o}/ryba.srt"]),
        ]
        app.go(steps, o)

    brow = ctk.CTkFrame(bro, fg_color="transparent")
    brow.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
    T.btn(brow, "Найти перебивки", "ghost", width=190,
          command=find_broll).pack(side="left")

    def autofill_broll():
        """Подставить CSV, если detect.py его уже делал."""
        p = Path(project()) / "broll.csv"
        if p.is_file():
            bro_csv.set(str(p))
            bro_note.configure(text=f"Найдено перебивок: {p.name}",
                                text_color=T.SUCCESS)
        else:
            bro_note.configure(text="CSV пока нет. Нажми «Найти перебивки».",
                                text_color=T.MUTED)

    T.btn(brow, "Подставить", "primary", width=140,
          command=autofill_broll).pack(side="left", padx=10)

    T.btn(wrap, "Собрать рыбу", "primary", width=180, command=run).pack(
        anchor="w", padx=T.PAD, pady=(0, T.PAD))
    panel_result.add_to(wrap, app, out)
    autofill_broll()
    return wrap


def page_review(parent, app):
    """Разбор готового ролика."""
    wrap = ctk.CTkScrollableFrame(parent, fg_color="transparent")
    src = T.card(wrap, "Готовый ролик")
    v = T.field(src, 0, "Видео", ctk.StringVar(),
                browse=lambda: app.pick_file(v, "Готовый ролик"))
    out = T.field(src, 1, "Куда сложить", ctk.StringVar(),
                  browse=lambda: app.pick_dir(out, "Куда сложить"))
    dev = T.device_row(src, 2)

    def run():
        val = app.need(v, "готовый ролик")
        if not val:
            return
        o = out.get().strip().strip('"') or str(BASE / "review_out")
        app.go([("Разбор монтажа", [str(MODES / "review.py"), "--input", val,
                                    "--outdir", o, "--device", dev.get()])], o)

    T.btn(wrap, "Разобрать", "primary", width=180, command=run).pack(
        anchor="w", padx=T.PAD, pady=(0, T.PAD))
    return wrap


def page_log(parent, app):
    """Заглушка: лог и так висит внизу окна постоянно."""
    wrap = ctk.CTkFrame(parent, fg_color="transparent")
    ctk.CTkLabel(wrap, text="Ход работы — внизу окна.\n\n"
                            "Пока идёт сборка, не закрывай AutoCut: "
                            "кнопка «Остановить» гасит и ffmpeg.",
                 text_color=T.MUTED, anchor="w", justify="left",
                 font=T.font(13)).pack(anchor="w", padx=T.PAD, pady=T.PAD)
    return wrap


SUBS = {
    "Интервью 2 камеры": "Две камеры, два микрофона, сборка в Premiere",
    "Новостной сюжет": "Лид, суть, синхроны, детали и финал по мероприятию",
    "Мультикам": "Настоящий мультикам в Premiere по общему таймкоду",
    "Настройки": "Пороги чистки речи и раскладки кадров",
    "Ход работы": "Что делает программа прямо сейчас",
    "Дайджест": "Папка исходников → раскладка по планам",
    "Интервью 1 видео": "Одна камера, одна дорожка",
    "Проверка готового": "Что лежит в собранном монтаже",
}

# Отдельных страниц «Таймлайн» и «Просмотр» больше нет. Разбор монтажа
# живёт внутри блока, который его собрал: чтобы посмотреть дорожки,
# не нужно гадать, в какой папке лежит результат.
TPL = [
    ("Интервью 2 камеры", page_multi),
    ("Новостной сюжет", pages_news.page_news),
    ("Мультикам", page_mc),
    ("Настройки", page_settings),
    ("Ход работы", page_log),
    ("Дайджест", page_digest),
    ("Интервью 1 видео", page_single),
    ("Проверка готового", page_review),
]

# Слева: сначала то, чем пользуешься каждый день, потом остальное.
GROUPS = [
    ("Основное", [("Интервью 2 камеры", page_multi),
                  ("Новостной сюжет", pages_news.page_news),
                  ("Мультикам", page_mc),
                  ("Настройки", page_settings),
                  ("Ход работы", page_log)]),
    ("Другие шаблоны", [("Дайджест", page_digest),
                        ("Интервью 1 видео", page_single),
                        ("Проверка готового", page_review)]),
]

FIELDS = 8

if __name__ == "__main__":
    App().mainloop()
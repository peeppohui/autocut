#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoCut — окно с кнопками. Терминал больше не нужен.
Запуск: двойной клик по AutoCut.bat (или python app.py).

Вкладки: Дайджест / Интервью-мультикамера / Интервью-одно / Проверка готового.
Каждая кнопка «Собрать» сама гоняет цепочку скриптов по порядку и пишет
русский лог внизу. Зависимости те же (requirements.txt), tkinter встроен в Python.
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

HERE = Path(__file__).resolve().parent        # plugin/src/ui
SRC = HERE.parent                            # plugin/src
BASE = SRC.parents[1]                        # корень проекта — рабочий cwd, как и раньше
PY = sys.executable

ML = SRC / "ml"                              # detect, sync, music
MODES = SRC / "modes"                        # autocut, interview, review
IO = SRC / "io"                              # premiere (FCP7 XML / EDL)


def run_steps(steps, log, done, state):
    """steps: [(заголовок, [аргументы])]. Стоп на первой ошибке или по отмене.
    state: {"proc": Popen|None, "cancel": bool} — для кнопки ОТМЕНА."""
    import os as _os
    env = dict(_os.environ)
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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AutoCut — сборка рыбы")
        self.geometry("760x620")
        self.q = queue.Queue()
        self.busy = False
        self.state = {"proc": None, "cancel": False}

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=False, padx=8, pady=8)
        self.tabs = {}
        for name in ("Дайджест", "Интервью 2 камеры", "Интервью 1 видео", "Проверка готового"):
            fr = ttk.Frame(nb, padding=10)
            nb.add(fr, text=name)
            self.tabs[name] = fr
        self.build_digest(self.tabs["Дайджест"])
        self.build_multi(self.tabs["Интервью 2 камеры"])
        self.build_single(self.tabs["Интервью 1 видео"])
        self.build_review(self.tabs["Проверка готового"])

        lf = ttk.LabelFrame(self, text="Лог", padding=6)
        lf.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.logw = tk.Text(lf, height=12, wrap="word", state="disabled")
        sb = ttk.Scrollbar(lf, command=self.logw.yview)
        self.logw.configure(yscrollcommand=sb.set)
        self.logw.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        bf = ttk.Frame(self)
        bf.pack(fill="x", padx=8, pady=(0, 8))
        self.cancel_btn = ttk.Button(bf, text="ОТМЕНА", command=self.cancel,
                                     state="disabled")
        self.cancel_btn.pack(side="left")
        self.open_btn = ttk.Button(bf, text="Открыть папку результата", command=self.open_result,
                                   state="disabled")
        self.open_btn.pack(side="right")
        self.result_dir = None
        self.after(120, self.pump)

    # ---------- helpers ----------
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

    def field(self, parent, label, row, is_dir=False, is_file=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        var = tk.StringVar()
        ent = ttk.Entry(parent, textvariable=var, width=62)
        ent.grid(row=row, column=1, sticky="ew", padx=6)
        def browse():
            if is_dir:
                v = filedialog.askdirectory(title=label)
            else:
                v = filedialog.askopenfilename(title=label,
                    filetypes=[("Все", "*.*")] if not is_file else is_file)
            if v:
                var.set(v)
        ttk.Button(parent, text="...", width=3, command=browse).grid(row=row, column=2)
        parent.columnconfigure(1, weight=1)
        return var

    def go(self, steps, result_dir):
        if self.busy:
            return
        self.busy = True
        self.state = {"proc": None, "cancel": False}
        self.result_dir = result_dir
        self.open_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        th = threading.Thread(target=run_steps,
                              args=(steps, self.log, self.finished, self.state),
                              daemon=True)
        th.start()

    def cancel(self):
        if not self.busy:
            return
        self.log("Отмена... останавливаю текущий шаг (добиваю ffmpeg).")
        self.state["cancel"] = True
        p = self.state.get("proc")
        if p is not None:
            kill_tree(p)

    def finished(self, ok):
        def _ui():
            self.busy = False
            self.cancel_btn.configure(state="disabled")
            self.log("ГОТОВО." if ok else "ОСТАНОВЛЕНО С ОШИБКОЙ — читай лог выше.")
            if ok and self.result_dir and Path(self.result_dir).exists():
                self.open_btn.configure(state="normal")
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

    def dev_row(self, parent, row):
        """Переключатель CPU/GPU. Авто = GPU если завелась, иначе тихо CPU."""
        ttk.Label(parent, text="Устройство:").grid(row=row, column=0, sticky="w", pady=3)
        var = tk.StringVar(value="auto")
        ttk.OptionMenu(parent, var, "auto", "auto", "cpu", "cuda").grid(
            row=row, column=1, sticky="w", padx=6)
        return var

    # ---------- вкладки ----------
    def build_digest(self, fr):
        src = self.field(fr, "Папка исходников:", 0, is_dir=True)
        out = self.field(fr, "Куда сложить (папка):", 1, is_dir=True)
        tgt = tk.StringVar(value="60")
        mus = tk.StringVar()
        ttk.Label(fr, text="Хронометраж, сек:").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(fr, textvariable=tgt, width=10).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Label(fr, text="Музыка (необязательно):").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(fr, textvariable=mus, width=62).grid(row=3, column=1, sticky="ew", padx=6)
        ttk.Button(fr, text="Выбрать", command=lambda: mus.set(
            filedialog.askopenfilename(title="Музыка") or mus.get())).grid(row=3, column=2)
        dev = self.dev_row(fr, 4)

        def run():
            s = self.need(src, "папку исходников")
            if not s:
                return
            o = out.get().strip().strip('"') or str(BASE / "digest_out")
            Path(o).mkdir(parents=True, exist_ok=True)
            t = tgt.get().strip() or "60"
            steps = [
                ("1. Определяю планы", [str(ML / "detect.py"), "--input", s, "--output", f"{o}/clips.csv",
                                        "--preview", f"{o}/preview", "--device", dev.get()]),
                ("2. Раскладываю по склейкам", [str(MODES / "autocut.py"), "--csv", f"{o}/clips.csv",
                                                "--mode", "digest", "--auto", "--finale-wide",
                                                "--target-sec", t, "--out", f"{o}/timeline.csv"]),
            ]
            if mus.get().strip():
                steps.append(("3. Подложка", [str(ML / "music.py"), "--music", mus.get().strip(),
                                             "--duration", t, "--out", f"{o}/bed.wav"]))
                steps.append(("4. XML для Premiere",
                              [str(IO / "premiere.py"), "--timeline", f"{o}/timeline.csv",
                               "--media-dir", s, "--out", f"{o}/ryba.xml",
                               "--edl", f"{o}/ryba.edl", "--trim-rec",
                               "--music", f"{o}/bed.wav"]))
            else:
                steps.append(("3. XML для Premiere",
                              [str(IO / "premiere.py"), "--timeline", f"{o}/timeline.csv",
                               "--media-dir", s, "--out", f"{o}/ryba.xml",
                               "--edl", f"{o}/ryba.edl", "--trim-rec"]))
            self.log("Проверь preview после шага 1: где уверенность <= 0.65 — поправь CSV и жми снова.")
            self.go(steps, o)
        ttk.Button(fr, text="СОБРАТЬ РЫБУ", command=run).grid(row=5, column=1, pady=10)

    def build_multi(self, fr):
        wide = self.field(fr, "Общий план (видео):", 0)
        gcam = self.field(fr, "Камера гостя (видео):", 1)
        gmic = self.field(fr, "Микрофон гостя:", 2)
        hmic = self.field(fr, "Микрофон ведущего:", 3)
        bro = self.field(fr, "Перебивки CSV (необязательно):", 4)
        out = self.field(fr, "Куда сложить (папка):", 5, is_dir=True)
        dev = self.dev_row(fr, 6)
        only_tx = tk.BooleanVar(value=False)
        ttk.Checkbutton(fr, text="Сначала только транскрипт (правлю текст, потом собираю)",
                        variable=only_tx).grid(row=7, column=1, sticky="w", pady=3)
        # Второй ракурс кладём на отдельную видеодорожку: Premiere не
        # понимает FCP7-multiclip, а две дорожки работают всегда.
        # Переключение потом - Edit > Toggle Switches.
        two_cam = tk.BooleanVar(value=True)
        ttk.Checkbutton(fr, text="Второй ракурс (две дорожки, Toggle Switches)",
                        variable=two_cam).grid(row=8, column=1, sticky="w", pady=3)
        txf = self.field(fr, "Правленый transcript.txt:", 10)

        def run():
            paths = {}
            for var, what in ((wide, "общий план"), (gcam, "камеру гостя"),
                              (gmic, "микрофон гостя"), (hmic, "микрофон ведущего")):
                v = self.need(var, what)
                if not v:
                    return
                paths[what] = v
            o = out.get().strip().strip('"') or str(BASE / "interview_out")
            Path(o).mkdir(parents=True, exist_ok=True)
            step2 = (["--broll", bro.get().strip()] if bro.get().strip() else []) + \
                    ["--outdir", f"{o}/rz", "--device", dev.get()]
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
                                       "--host-mic", f"{o}/synced/mic_host.wav"] + step2),
            ]
            if not only_tx.get():
                steps.append(
                    ("3. XML для Premiere",
                     [str(IO / "premiere.py"), "--timeline", f"{o}/rz/selects.csv", "--media-dir", o,
                      "--out", f"{o}/ryba.xml", "--edl", f"{o}/ryba.edl", "--trim-rec"]
                     + (["--stacked"] if two_cam.get() else [])),
                )
                steps.append(("4. Субтитры",
                              [str(IO / "srt.py"), "--selects", f"{o}/rz/selects.csv",
                               "--out", f"{o}/ryba.srt"]))
            else:
                self.log("Режим транскрипта: после шага 2 вычеркни мусор в rz/transcript.txt "
                         "строками с '-' и жми нижнюю кнопку.")
            self.go(steps, o)
        ttk.Button(fr, text="СОБРАТЬ РЫБУ", command=run).grid(row=9, column=1, pady=10,
                                                              sticky="e")

        def run_from_tx():
            t = txf.get().strip().strip('"')
            if not t or not Path(t).is_file():
                messagebox.showwarning("AutoCut", "Укажи правленый transcript.txt")
                return
            w = self.need(wide, "общий план")
            g = self.need(gcam, "камеру гостя")
            if not w or not g:
                return
            o = out.get().strip().strip('"') or str(BASE / "interview_out")
            Path(o).mkdir(parents=True, exist_ok=True)
            steps = [
                ("1. Сборка из правленого текста",
                 [str(MODES / "interview.py"), "--wide", w, "--guest-cam", g, "--outdir", f"{o}/rz",
                  "--from-transcript", t]
                 + (["--broll", bro.get().strip()] if bro.get().strip() else [])),
                ("2. XML для Premiere",
                 [str(IO / "premiere.py"), "--timeline", f"{o}/rz/selects.csv", "--media-dir", o,
                  "--out", f"{o}/ryba.xml", "--edl", f"{o}/ryba.edl", "--trim-rec"]
                 + (["--stacked"] if two_cam.get() else [])),
                ("3. Субтитры",
                 [str(IO / "srt.py"), "--selects", f"{o}/rz/selects.csv",
                  "--out", f"{o}/ryba.srt"]),
            ]
            self.go(steps, o)
        ttk.Button(fr, text="СОБРАТЬ ИЗ ПРАВЛЕНОГО ТЕКСТА",
                   command=run_from_tx).grid(row=11, column=1, pady=6)

    def build_single(self, fr):
        src = self.field(fr, "Видео интервью:", 0)
        bro = self.field(fr, "Перебивки CSV (необязательно):", 1)
        out = self.field(fr, "Куда сложить (папка):", 2, is_dir=True)
        dev = self.dev_row(fr, 3)

        def run():
            v = self.need(src, "видео интервью")
            if not v:
                return
            o = out.get().strip().strip('"') or str(BASE / "interview_out")
            Path(o).mkdir(parents=True, exist_ok=True)
            steps = [
                ("1. Рыба по тексту", [str(MODES / "interview.py"), "--input", v, "--outdir", f"{o}/rz",
                                       "--device", dev.get()]
                 + (["--broll", bro.get().strip()] if bro.get().strip() else [])),
                ("2. XML для Premiere",
                 [str(IO / "premiere.py"), "--timeline", f"{o}/rz/selects.csv", "--media-dir", o,
                  "--out", f"{o}/ryba.xml", "--edl", f"{o}/ryba.edl", "--trim-rec"]),
                ("3. Субтитры",
                 [str(IO / "srt.py"), "--selects", f"{o}/rz/selects.csv",
                  "--out", f"{o}/ryba.srt"]),
            ]
            self.go(steps, o)
        ttk.Button(fr, text="СОБРАТЬ РЫБУ", command=run).grid(row=4, column=1, pady=10)

    def build_review(self, fr):
        src = self.field(fr, "Готовый ролик:", 0)
        out = self.field(fr, "Куда сложить (папка):", 1, is_dir=True)
        dev = self.dev_row(fr, 2)

        def run():
            v = self.need(src, "готовый ролик")
            if not v:
                return
            o = out.get().strip().strip('"') or str(BASE / "review_out")
            self.go([("Разбор монтажа", [str(MODES / "review.py"), "--input", v, "--outdir", o,
                                         "--device", dev.get()])], o)
        ttk.Button(fr, text="РАЗОБРАТЬ", command=run).grid(row=3, column=1, pady=10)


if __name__ == "__main__":
    App().mainloop()

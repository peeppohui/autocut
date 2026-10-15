# -*- coding: utf-8 -*-
"""Страница «Новостной сюжет».

Блок самодостаточный: он сам синхронизирует камеры, сам распознаёт
речь и сам собирает сюжет. Раньше здесь стояла инструкция «сначала
прогони Интервью 2 камеры» - то есть заставляли прыгать в другой блок.
Разделение на блоки существует как раз для того, чтобы работать в том,
который нужен, поэтому все шаги живут здесь.

Что делает логика раскладки: modes/news_package.py, она покрыта
tests/test_news.py. Здесь только форма и цепочка шагов.
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
ML = SRC / "ml"
MODES = SRC / "modes"
IO = SRC / "io"

sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "config"))

import theme as T                            # noqa: E402
import panel_result                          # noqa: E402
import steps as STEPS                        # noqa: E402

VIDEO_EXT = (".mp4", ".mov", ".mxf", ".mkv", ".avi", ".m4v")
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


def synced_paths(project_dir) -> dict:
    """Что лежит в папке проекта после синхронизации и распознавания.

    Имена совпадают с тем, что кладёт ml/sync.py, поэтому поля формы
    можно заполнить без блуждания по диску.
    """
    p = Path(project_dir)
    sd = p / "synced"
    vids = [sd / "ref.mp4", sd / "cam.mp4"]
    # Порядок гостю, потом ведущему. Он обязан совпадать с тем, как
    # микрофоны подписаны на шаге синхронизации (mic1 -> --mic-guest),
    # и с порядком аргументов у interview.py. Раньше список был
    # обратным, и микрофон ВЕДУЩЕГО уходил в --guest-mic: все его слова
    # помечались как слова гостя и так уезжали в титры сюжета.
    mics = [sd / "mic_guest.wav", sd / "mic_host.wav"]
    return {
        "videos": [str(v) for v in vids if v.is_file()],
        "mics": [str(m) for m in mics if m.is_file()],
        "transcript": str(p / "rz" / "transcript.txt")
        if (p / "rz" / "transcript.txt").is_file() else "",
        "ready": sd.is_dir(),
    }


class SyncRow(ctk.CTkFrame):
    """Один синхрон: кто говорит, на какой картинке и в каком файле речь.

    У каждого поля своя подпись слева. Одних подсказок внутри полей
    не хватало: на тёмном фоне их почти не видно, и человек не понимал,
    куда что вставлять.
    """

    def __init__(self, parent, app, n: int, on_remove):
        super().__init__(parent, fg_color=T.RAISED, corner_radius=8)
        self.grid_columnconfigure(1, weight=1)

        self.speaker = ctk.StringVar()
        self.role = ctk.StringVar()
        self.media = ctk.StringVar()
        self.audio = ctk.StringVar()
        self.transcript = ctk.StringVar()

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=3, sticky="ew",
                  padx=(12, 8), pady=(6, 0))
        ctk.CTkLabel(head, text=f"Синхрон {n}", text_color=T.MUTED,
                     anchor="w", font=T.font(12, "bold")).pack(side="left")
        T.btn(head, "Убрать", "danger", width=92, height=28,
              command=lambda: on_remove(self)).pack(side="right")

        pick = app.pick_file
        F = T.field
        F(self, 1, "Имя", self.speaker, "Анна Ковалёва", width=104)
        F(self, 2, "Должность", self.role, "директор", width=104)
        F(self, 3, "Видео", self.media, r"нажми «Подставить» или «…»",
          browse=lambda: pick(self.media, "Видео синхрона",
                              [("Видео", "*.mp4 *.mov *.mxf *.mkv")]),
          width=104)
        F(self, 4, "Микрофон", self.audio, r"то же самое",
          browse=lambda: pick(self.audio, "Микрофон спикера",
                              [("Звук", "*.wav *.mp3 *.m4a")]), width=104)
        F(self, 5, "Транскрипт", self.transcript,
          r"rz\transcript.txt — кнопкой выше",
          browse=lambda: pick(self.transcript, "Транскрипт",
                              [("Текст", "*.txt *.json")]), width=104)
        ctk.CTkLabel(self, text="", height=4).grid(
            row=6, column=0, columnspan=3)

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

    def fill(self, media="", audio="", transcript=""):
        if media:
            self.media.set(str(media))
        if audio:
            self.audio.set(str(audio))
        if transcript:
            self.transcript.set(str(transcript))


def page_news(parent, app):
    """Новостной сюжет: лид, суть, синхроны, детали, финал."""
    wrap = ctk.CTkScrollableFrame(parent, fg_color="transparent")

    # ------------------------------------------------------- куда
    dst = T.card(wrap, "Куда сложить", "Все шаги ниже кладут результат сюда")
    out = ctk.StringVar(value=str(BASE / "news_out"))
    T.field(dst, 0, "Папка проекта", out, browse=lambda: app.pick_dir(
        out, "Куда сложить"))

    def project():
        return out.get().strip().strip('"') or str(BASE / "news_out")

    # ------------------------------------------- 1. подготовка
    prep = T.card(wrap, "1. Подготовка",
                  "Свои файлы, свои шаги. Если синхронизация уже есть — "
                  "жми сразу «Подставить»")
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

    note = ctk.CTkLabel(prep, text="", text_color=T.MUTED, anchor="w",
                        font=T.font(11), wraplength=620, justify="left")
    note.grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))

    # ------------------------------------------------------- синхроны
    syn = T.card(wrap, "2. Синхроны",
                 "Кого берём в сюжет. Лид программа выбирает сама — "
                 "по цифрам, эмоциям и конфликту в речи")
    box = ctk.CTkFrame(syn, fg_color="transparent")
    box.grid(row=0, column=0, columnspan=3, sticky="ew")

    rows = []

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

    cnt = ctk.CTkLabel(box, text="", text_color=T.FAINT, anchor="w",
                       font=T.font(11))
    cnt.grid(row=0, column=0, sticky="w", pady=(0, 4))
    add_row()      # одна строка сразу: пустая форма пугает

    bar = ctk.CTkFrame(box, fg_color="transparent")
    bar.grid(row=99, column=0, sticky="w", pady=(2, 4))
    T.btn(bar, "+ Ещё спикер", "ghost", width=150,
          command=add_row).pack(side="left")

    # ----------------------------------------------- 3. перебивки
    br = T.card(wrap, "3. Перебивки",
                "Видео, которым прикрывают речь: зал, толпа, аппаратура. "
                "Под синхрон длиннее 10 с перебивка вставляется сама")
    broll_dir = ctk.StringVar()
    T.field(br, 0, "Папка", broll_dir,
            r"например D:\Съёмка\мероприятие\broll",
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

    # ------------------------------------------------- 4. закадр
    vo = T.card(wrap, "4. Закадр",
                "Голос ведущего, записанный заранее. Ложится поверх "
                "перебивок в блоках «суть» и «детали»")
    vo_audio = ctk.StringVar()
    T.field(vo, 0, "Файл закадра", vo_audio, r"например vo\nadkadr.wav",
            browse=lambda: app.pick_file(vo_audio, "Закадр",
                                         [("Звук", "*.wav *.mp3 *.m4a")]))

    # ------------------------------------------------- 5. стендап
    st = T.card(wrap, "5. Стендап для финала",
                "Короткая реплика «в камеру» на отдельной камере. "
                "Необязательно: без неё финал возьмёт свободный синхрон")
    st_media = ctk.StringVar()
    st_audio = ctk.StringVar()
    st_tx = ctk.StringVar()
    T.field(st, 0, "Видео", st_media, r"synced\cam.mp4 — нажми «Подставить»",
            browse=lambda: app.pick_file(st_media, "Видео стендапа"))
    T.field(st, 1, "Микрофон", st_audio, r"synced\mic_guest.wav",
            browse=lambda: app.pick_file(st_audio, "Микрофон стендапа"))
    T.field(st, 2, "Транскрипт", st_tx, r"rz\transcript.txt",
            browse=lambda: app.pick_file(st_tx, "Транскрипт стендапа",
                                         [("Текст", "*.txt *.json")]))

    # ------------------------------------------------- 6. титры
    ti = T.card(wrap, "6. Титры",
                "По строке на человека в формате «Имя — Должность». "
                "Попадают в titles.csv. Пусто — не будет титров")
    titles_txt = ctk.CTkTextbox(ti, height=100, corner_radius=8,
                                fg_color=T.RAISED, border_color=T.BORDER,
                                border_width=1, text_color=T.TEXT,
                                font=T.font(13), wrap="word")
    titles_txt.grid(row=0, column=0, columnspan=3, sticky="ew")

    # ------------------------------------------------------- сборка
    def vo_path(src: str) -> str:
        """Готовый путь к закадру нужной громкости.

        Файл пользователя не трогаем: закадр редко бывает один и тот
        же, и если бы мы правили его на месте, то второй запуск сюжета
        считал бы уже нормализованный звук и поднял его ещё раз.
        Поэтому копия рядом, в папке проекта, а исходник как был.
        """
        import loudness
        src_p = Path(src)
        if not src_p.is_file() or not loudness.enabled():
            return str(src_p)
        out_dir = Path(project()) / "voice"
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / f"vo_{src_p.name}"
        rep = loudness.normalize(src_p, dst)
        note.configure(text=loudness.describe(rep), text_color=T.MUTED)
        return str(dst) if rep.get("ok") else str(src_p)

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
                bad.append(f"Видео синхрона «{r.label()}» не найденo: "
                           f"{d['media']}")
            if d.get("transcript") and not Path(d["transcript"]).is_file():
                bad.append(f"Транскрипт синхрона «{r.label()}» не найден: "
                           f"{d['transcript']}")
            syncs.append(d)

        if not syncs:
            bad.append("Заполни хотя бы одного синхрона: без них сюжет "
                       "не собрать.")
        elif not any(s.get("transcript") for s in syncs):
            bad.append("Нужен транскрипт хотя бы у одного синхрона: "
                       "по нему выбирается лид. Жми «Распознать речь».")

        spec = {"syncs": syncs}
        broll = scan_broll(broll_dir.get())
        if broll:
            spec["broll"] = broll
        va = vo_audio.get().strip().strip('"')
        if va:
            spec["vo"] = {"audio": vo_path(va)}
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

    def autofill():
        """Заполняет поля тем, что уже есть в папке проекта.

        Отвечает на вопрос «что куда вставлять»: после синхронизации
        и распознавания пути известны заранее, искать их по диску не
        нужно.
        """
        sp = synced_paths(project())
        if not (sp["videos"] or sp["transcript"]):
            note.configure(text="В папке проекта пока нет папки synced. "
                                "Заполни синхронизацию выше.",
                           text_color=T.MUTED)
            return False
        while len(rows) < max(1, len(sp["videos"]), len(sp["mics"])):
            add_row()
        for i, r in enumerate(rows):
            r.fill(media=sp["videos"][i] if i < len(sp["videos"]) else "",
                   audio=sp["mics"][i] if i < len(sp["mics"]) else "",
                   transcript=sp["transcript"])
        if sp["videos"] and not st_media.get().strip():
            st_media.set(sp["videos"][-1])
            if sp["mics"]:
                st_audio.set(sp["mics"][-1])
            if sp["transcript"]:
                st_tx.set(sp["transcript"])
        note.configure(
            text=f"Подставлено из папки проекта: видео {len(sp['videos'])}, "
                 f"микрофонов {len(sp['mics'])}"
                 + (", транскрипт есть" if sp["transcript"]
                    else ", транскрипта нет — жми «Распознать речь»"),
            text_color=T.SUCCESS if sp["transcript"] else T.MUTED)
        recount()
        return True

    def do_sync():
        w = app.need(wide, "общий план")
        if not w:
            return
        c = cam2.get().strip().strip('"')
        argv = [str(ML / "sync.py"), "--ref", w,
                "--outdir", f"{project()}/synced"]
        if c and Path(c).is_file():
            argv += ["--cam", c]
        for mic in (mic1, mic2):
            m = mic.get().strip().strip('"')
            if m and Path(m).is_file():
                argv += ["--mic-guest" if mic is mic1 else "--mic-host", m]
        app.go([("Синхронизация", argv)], project())

    def do_transcribe():
        sp = synced_paths(project())
        if not sp["videos"]:
            app.warn("Сначала синхронизируй: в папке проекта нет synced.")
            return
        argv = [str(MODES / "interview.py"), "--transcript-only",
                "--device", dev.get(), "--outdir", f"{project()}/rz"]
        if len(sp["videos"]) > 1:
            argv += ["--wide", sp["videos"][0], "--guest-cam", sp["videos"][1]]
        else:
            argv += ["--input", sp["videos"][0]]
        if sp["mics"]:
            argv += ["--guest-mic", sp["mics"][0]]
        if len(sp["mics"]) > 1:
            argv += ["--host-mic", sp["mics"][1]]
        app.go([("Распознаю речь", argv)], project())

    def run_story():
        spec, bad = collect()
        if bad:
            app.warn("\n".join(bad[:6]))
            return
        o = project()
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
             STEPS.premiere_args(f"{rz}/selects.csv", o)),
            ("3. Субтитры", STEPS.srt_args(f"{rz}/selects.csv", o)),
        ], o)

    # ------------------------------------------------------- кнопки
    prow = ctk.CTkFrame(prep, fg_color="transparent")
    prow.grid(row=6, column=0, columnspan=3, sticky="w", pady=(4, 8))
    T.btn(prow, "Синхронизировать", "ghost", width=190,
          command=do_sync).pack(side="left")
    T.btn(prow, "Распознать речь", "ghost", width=190,
          command=do_transcribe).pack(side="left", padx=10)
    T.btn(prow, "Подставить", "primary", width=150,
          command=autofill).pack(side="left")
    ctk.CTkLabel(prow, text="← подставляет видео, микрофон и транскрипт "
                            "из папки проекта",
                 text_color=T.FAINT, font=T.font(11)).pack(side="left",
                                                           padx=10)

    row = ctk.CTkFrame(wrap, fg_color="transparent")
    row.pack(fill="x", padx=T.PAD, pady=(0, T.PAD))
    T.btn(row, "Собрать сюжет", "primary", width=200,
          command=run_story).pack(side="left")

    spec_example = SRC.parent / "assets" / "news_spec_example.json"
    if spec_example.is_file():
        T.btn(row, "Открыть образец spec.json", "quiet",
              command=lambda: os.startfile(str(spec_example))
              ).pack(side="left", padx=10)

    panel_result.add_to(wrap, app, out)

    # если папка проекта уже собрана - показываем сразу, без кнопки
    autofill()
    return wrap

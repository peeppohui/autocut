# -*- coding: utf-8 -*-
"""Тест окна AutoCut: собираются ли страницы и те ли шаги.

Главное здесь - не красота, а сохранность логики. Окно переезжало
с ttk на customtkinter, и самое страшное - потерять или переставить
шаг в цепочке сборки: такое не падает, а тихо делает не то.
"""
import sys
import tempfile
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src" / "ui"))
import bootstrap  # noqa: F401,F403

import app as A

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


captured = []
A.App.go = lambda self, steps, result_dir: captured.append(steps)

# Кнопки, которым не хватает пути, показывают модальное окно. В тесте
# оно остановит прогон навсегда - подменяем заглушкой, которая
# просто запоминает, что окно было.
warned = []
A.messagebox.showwarning = lambda *a, **k: warned.append(a[-1])
A.messagebox.showinfo = lambda *a, **k: None

app = A.App()
app.update_idletasks()


def all_widgets(w):
    out = []
    for c in w.winfo_children():
        out.append(c)
        out.extend(all_widgets(c))
    return out


def fill_and_press(page_name, label, values=()):
    """Заполнить поля и нажать кнопку на ОДНОЙ сборке страницы.

    Нельзя звать show() между заполнением и нажатием: страница
    пересоздаётся, поля с введёнными значениями исчезают.
    """
    app.show(page_name)
    app.update_idletasks()
    page = app.host.winfo_children()[0]
    ents = [w for w in all_widgets(page)
            if isinstance(w, A.ctk.CTkEntry)]
    for e, v in zip(ents, values):
        e.cget("textvariable").set(v)
    for w in all_widgets(page):
        if isinstance(w, A.ctk.CTkButton) and w.cget("text") == label:
            w.invoke()
            return True
    return False


# Кнопки проверяют существование пути, поэтому подставляем то, что
# заведомо есть у любого: настоящий временный файл. Чужой путь с
# ноутбука автора в тесте означал бы, что у другого машины он не пройдёт.
FAKE = _P(tempfile.mkdtemp()) / "interview.mp4"
FAKE.write_bytes(b"\0" * 16)
FAKE_DIR = str(FAKE.parent)

# --- все страницы собираются и не пустые ---
# число берём из GROUPS, а не пишем константой: добавили «Мультикам» -
# и тест молча продолжил бы проверять старое количество
names = [n for _, items in A.GROUPS for n, _ in items]
tpl = [n for n, _ in A.TPL]
ok("страницы в навигации совпадают со списком",
   sorted(names) == sorted(tpl),
   f"{sorted(names)} против {sorted(tpl)}")
ok("навигация не пустая", len(names) >= 7, str(len(names)))
empty = []
for n in names:
    app.show(n)
    app.update_idletasks()
    if not app.host.winfo_children():
        empty.append(n)
ok("ни одна страница не пустая", not empty, str(empty))
ok("лог на месте и виден", app.logw.winfo_manager() != "")

# --- кнопка не должна подменять состояние Tkinter ---
# Раньше словарь состояния назывался self.state и затирал метод Tk,
# на котором держится про заголовок окна. customtkinter падал сразу.
ok("self.state не затирает метод Tk", not isinstance(app.state, dict),
   type(app.state).__name__)
ok("состояние запуска отдельно", isinstance(app.run_state, dict))

# --- цепочки шагов те же, что до переезда ---
ok("дайджест: кнопка отправила шаги",
   fill_and_press("Дайджест", "Собрать рыбу", [FAKE_DIR]) and bool(captured))
d = [t for t, _ in captured[-1]]
ok("дайджест: шаги по порядку",
   d == ["1. Определяю планы", "2. Раскладываю по склейкам",
         "3. XML для Premiere"], str(d))
argv = dict(captured[-1])["2. Раскладываю по склейкам"]
ok("дайджест: режим digest на месте", "--mode" in argv
   and "digest" in argv, str(argv[:6]))

fill_and_press("Интервью 1 видео", "Собрать рыбу", [str(FAKE)])
one = [t for t, _ in captured[-1]]
ok("одно видео: шаги по порядку",
   one == ["1. Рыба по тексту", "2. XML для Premiere", "3. Субтитры"],
   str(one))

# без файлов кнопка обязана просить, а не падать и не вешать тест
before = len(captured)
fill_and_press("Интервью 1 видео", "Собрать рыбу")
ok("без исходника шаги не запускаются", len(captured) == before)
ok("и пользователю сказано, чего не хватает", bool(warned), str(warned))

# --- новостной сюжет ---
# Логику раскладки проверяет test_news.py. Здесь - форма: разбор полей,
# валидация и цепочка шагов.
PN = A.pages_news

ok("титры «Имя — Должность» разбираются",
   PN.parse_titles("Иван — журналист\nПётр") ==
   [{"name": "Иван", "role": "журналист"}, {"name": "Пётр", "role": ""}],
   str(PN.parse_titles("Иван — журналист\nПётр")))
ok("титры переживают пустые строки",
   PN.parse_titles("\n  \nА — Б\n\n") == [{"name": "А", "role": "Б"}],
   str(PN.parse_titles("\n  \nА — Б\n\n")))
ok("перебивки берутся из папки",
   [_P(p).name for p in PN.scan_broll(FAKE_DIR)] == ["interview.mp4"],
   str(PN.scan_broll(FAKE_DIR)))
ok("перебивки из чужой папки не роняют программу",
   PN.scan_broll("нет/такой/папки") == [])

# строка спикера добавляется и удаляется
app.show("Новостной сюжет")
app.update_idletasks()
page = app.host.winfo_children()[0]
add_btn = [w for w in all_widgets(page)
           if isinstance(w, A.ctk.CTkButton) and w.cget("text") == "+ Ещё спикер"]
ok("кнопка добавления спикера есть", bool(add_btn))
n0 = len([w for w in all_widgets(page) if isinstance(w, PN.SyncRow)])
if add_btn:
    add_btn[0].invoke()
    app.update_idletasks()
n1 = len([w for w in all_widgets(page) if isinstance(w, PN.SyncRow)])
ok("спикер добавляется", n1 == n0 + 1, f"{n0} -> {n1}")

# без единого синхрона кнопка обязана объяснить и не запустить шаги
before = len(captured)
warned.clear()
fill_and_press("Новостной сюжет", "Собрать сюжет")
ok("пустой сюжет не запускает сборку", len(captured) == before)
ok("и объясняет, чего не хватает", bool(warned), str(warned))

# полная сборка: заполняем первого спикера. Порядок полей соответствует
# порядку их появления на странице: спикер, перебивки, закадр, стендап,
# папка результата. Закадр и стендап оставляем пустыми - они не обязательны
tx = FAKE.parent / "transcript.txt"
tx.write_text("Ну вот в 2024 году произошёл рекорд и скандал",
              encoding="utf-8")
fill_and_press("Новостной сюжет", "Собрать сюжет",
               [str(FAKE), "продюсер", str(FAKE), "", str(tx),
                "", "", "", "", "", FAKE_DIR])
news_steps = [t for t, _ in captured[-1]]
ok("сюжет: шаги по порядку",
   news_steps == ["1. Раскладка сюжета", "2. XML для Premiere", "3. Субтитры"],
   str(news_steps))
news_argv = dict(captured[-1])["1. Раскладка сюжета"]
ok("сюжет: spec и outdir передаются",
   "--spec" in news_argv and "--outdir" in news_argv, str(news_argv))
prem_argv = dict(captured[-1])["2. XML для Premiere"]
ok("сюжет: premiere получает selects.csv сюжета",
   prem_argv[prem_argv.index("--timeline") + 1].replace("\\", "/")
   .endswith("rz/selects.csv"), str(prem_argv))
# главное: spec.json должен существовать и быть валидным, иначе шаг 1
# упадёт уже после того, как пользователь отсидит всю сборку
spec_path = _P(FAKE_DIR) / "spec.json"
ok("spec.json создан", spec_path.is_file(), str(spec_path))
if spec_path.is_file():
    import json as _json
    spec = _json.loads(spec_path.read_text(encoding="utf-8"))
    ok("в spec попал синхрон с видео",
       spec.get("syncs") and spec["syncs"][0].get("media") == str(FAKE),
       str(spec.get("syncs"))[:200])
    ok("у синхрона есть транскрипт",
       bool((spec.get("syncs") or [{}])[0].get("transcript")))
    ok("пустые строки спикера не попали в spec",
       len(spec.get("syncs", [])) == 1, str(len(spec.get("syncs", []))))
    ok("без перебивок поле broll не добавляется", "broll" not in spec,
       str(spec.get("broll")))
    ok("без закадра поле vo не добавляется", "vo" not in spec)
    ok("без стендапа поле standup не добавляется", "standup" not in spec)

# та же сборка, но перебивки указаны - поле обязано появиться в spec
fill_and_press("Новостной сюжет", "Собрать сюжет",
               [str(FAKE), "продюсер", str(FAKE), "", str(tx),
                FAKE_DIR, "", "", "", "", FAKE_DIR])
spec2 = _json.loads(spec_path.read_text(encoding="utf-8"))
ok("перебивки попадают в spec",
   [_P(p).name for p in spec2.get("broll", [])] == ["interview.mp4"],
   str(spec2.get("broll")))

# синхрон без транскрипта - лид не выбрать, это надо сказать заранее
warned.clear()
before = len(captured)
fill_and_press("Новостной сюжет", "Собрать сюжет",
               [str(FAKE), "", str(FAKE), "", "",
                FAKE_DIR, "", "", "", "", FAKE_DIR])
ok("синхрон без транскрипта не запускает сборку", len(captured) == before)
ok("и предупреждение про транскрипт есть",
   any("транскрипт" in w.lower() for w in warned), str(warned))

app.destroy()
print()
if fail:
    print(f"UI FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("UI OK")

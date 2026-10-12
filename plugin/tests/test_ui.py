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

app.destroy()
print()
if fail:
    print(f"UI FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("UI OK")

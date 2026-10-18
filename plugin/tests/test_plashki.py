# -*- coding: utf-8 -*-
"""Плашки: ФИО, должность, таймкоды и правило пяти секунд.

Проверяется то, что ломается молча: если длительности посчитать не в
тех единицах, плашек не будет ни одной, и это будет выглядеть как
«персон нет», а не как ошибка.
"""
import shutil
import sys
import tempfile
from pathlib import Path as _P

ROOT = _P(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "io"))
sys.path.insert(0, str(ROOT / "src" / "config"))
import bootstrap  # noqa: F401,F403

import plashki as P  # noqa: E402

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


WHO = {"гость": {"fio": "Виктория Лещенко", "position": "СТУДЕНТКА"},
       "ведущий": {"fio": "Иван Морозов", "position": "КОРРЕСПОНДЕНТ"}}
FPS = 25


def clip(hero, dur, used=None):
    c = {"hero": hero, "duration": dur}
    if used is not None:
        c["_used"] = used
    return c


# --- длительность приходит и в секундах, и в кадрах ---
e, _ = P.plan([clip("гость", 10.0)], WHO, 5.0, FPS)
ok("длительность из раскладки (секунды) понимается верно",
   len(e) == 1 and e[0]["dur"] == 10.0, str(e))
e, _ = P.plan([clip("гость", 0.0, used=250)], WHO, 5.0, FPS)
ok("длительность из premiere.py (кадры) переводится в секунды",
   len(e) == 1 and abs(e[0]["dur"] - 10.0) < 1e-9, str(e))

# --- правило пяти секунд ---
e, notes = P.plan([clip("ведущий", 4.6), clip("гость", 12.0)], WHO, 5.0, FPS)
ok("короткое появление плашки не получает",
   [x["fio"] for x in e] == ["Виктория Лещенко"], str([x["fio"] for x in e]))
ok("и об этом сказано словами, а не молча",
   any("Иван Морозов" in n for n in notes), str(notes))
ok("в объяснении есть и причина",
   any("5с" in n for n in notes), str(notes))

e, notes = P.plan([clip("ведущий", 5.0)], WHO, 5.0, FPS)
ok("ровно пять секунд плашку получают", len(e) == 1, str(e))

# --- соседние куски одного человека склеиваются в одну плашку ---
e, _ = P.plan([clip("гость", 8.0), clip("гость", 8.0), clip("гость", 8.0)],
              WHO, 5.0, FPS)
ok("три куска подряд одного человека = одна плашка", len(e) == 1, str(e))
e, _ = P.plan([clip("гость", 8.0), clip("гость", 8.0), clip("гость", 8.0)],
              WHO, 5.0, FPS, every_appearance=True)
ok("а с --every-appearance плашка на каждом появлении", len(e) == 3, str(e))

# --- после чужого плашка снова нужна ---
e, _ = P.plan([clip("гость", 8.0), clip("ведущий", 8.0), clip("гость", 8.0)],
              WHO, 5.0, FPS)
ok("после чужого плашка появляется снова", len(e) == 3, str(e))

# --- таймкод ---
e, _ = P.plan([clip("гость", 10.0), clip("ведущий", 10.0)], WHO, 5.0, FPS)
ok("второе появление начинается сразу за первым",
   abs(e[1]["start"] - 10.0) < 1e-9, str(e[1]["start"]))

# --- неизвестная роль молчит, а не падает ---
e, _ = P.plan([clip("перебивка", 30.0)], WHO, 5.0, FPS)
ok("перебивка без ФИО плашки не вызывает и не ломает сборку", e == [], str(e))

# --- отчёт ---
tmp = _P(tempfile.mkdtemp(prefix="autocut_plashki_"))
e, notes = P.plan([clip("ведущий", 4.6), clip("гость", 12.0)], WHO, 5.0, FPS)
rep = tmp / "plashki.txt"
P.write_report(e, rep, "SVS_low_third", notes)
txt = rep.read_text(encoding="utf-8")
ok("в отчёте есть ФИО", "Виктория Лещенко" in txt, txt[:80])
ok("и должность", "СТУДЕНТКА" in txt, "")
ok("и имя шаблона", "SVS_low_third" in txt, "")
ok("таймкод в том же виде, что в Premiere", "00:00:04:15" in txt, txt)
ok("и пропущенный человек назван", "Иван Морозов" in txt, "")
ok("правило «Имя Фамилия» напомнено", "Имя Фамилия" in txt, "")

# --- картинка ---
if not shutil.which("ffmpeg"):
    print("пропуск картинки")
else:
    png = tmp / "lt.png"
    made = P.render("Виктория Лещенко",
                    "СТУДЕНТКА НАПРАВЛЕНИЯ «ТЕЛЕВИДЕНИЕ» СГТУ ИМЕНИ ГАГАРИНА Ю.А.",
                    png)
    ok("плашка нарисовалась", made and png.is_file(), str(png))
    if png.is_file():
        from PIL import Image
        im = Image.open(png)
        ok("размер кадра секвенции", im.size == (1920, 1080), str(im.size))
        ok("с прозрачным фоном", im.mode == "RGBA", im.mode)
        # планка должна быть в нижней трети и не занимать весь кадр
        alpha = im.split()[-1]
        box = alpha.getbbox()
        ok("планка не пустая и не на весь кадр",
           box is not None and box[2] - box[0] < 1920 * 0.9,
           str(box))
        ok("планка в нижней части кадра", box[1] > 1080 * 0.5, str(box))
        # кириллица в имени файла обязана выжить (Windows + PIL)
        ok("кириллица в имени файла не ломает запись",
           P.render("Иван Морозов", "КОРРЕСПОНДЕНТ", tmp / "Иван Морозов.png"))

shutil.rmtree(tmp, ignore_errors=True)
print("PLASHKI " + ("OK" if not fail else "FAIL " + ", ".join(fail)))
sys.exit(1 if fail else 0)
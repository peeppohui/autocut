# -*- coding: utf-8 -*-
"""Тесты SRT: формат времени, очистка текста, сборка файла.

Таймкод в SRT — реальное время с запятой в миллисекундах, drop-frame
не применяется. Ошибка на миллисекунде уезжает на эфире, поэтому
формат проверяем жёстко.
"""
import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
import bootstrap  # noqa: F401,F403

from srt import fmt_time, clean_text, build_srt, wrap_text

fail = []


def eq(name, got, want):
    if got == want:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}\n     получено {got!r}\n     ждалось   {want!r}")


# --- формат времени ---
eq("0 секунд", fmt_time(0), "00:00:00,000")
eq("дробная секунда", fmt_time(1.5), "00:00:01,500")
eq("минута", fmt_time(61.25), "00:01:01,250")
eq("час", fmt_time(3600), "01:00:00,000")
eq("2 часа 3 мин 4.567 с", fmt_time(7384.567), "02:03:04,567")
eq("разделитель доли — запятая", fmt_time(1.5)[8], ",")
eq("миллисекунды с нуля", fmt_time(0.001), "00:00:00,001")
eq("точность до 1 мс", fmt_time(1 / 25), "00:00:00,040")
eq("25 fps: кадр = 40 мс", fmt_time(75 * (1 / 25)), "00:00:03,000")

# --- отрицательное и мусор ---
eq("отрицательное -> ноль", fmt_time(-5), "00:00:00,000")
eq("строка вместо числа", fmt_time("abc"), "00:00:00,000")
eq("None", fmt_time(None), "00:00:00,000")

# --- очистка текста ---
eq("метка [?] убирается", clean_text("[?] ВЕДУЩИЙ: привет"), "привет")
eq("метка [~] убирается", clean_text("[~] ГОСТЬ: да"), "да")
eq("метка [!] убирается", clean_text("[!] ГОСТЬ: ветер"), "ветер")
# метка в selects стоит ПОСЛЕ имени спикера - раньше доезжала до субтитров
eq("метка после имени спикера",
   clean_text("ГОСТЬ: [!] привет, Катя."), "привет, Катя.")
eq("метка после имени, имя сохранено",
   clean_text("ГОСТЬ: [~] привет", False), "ГОСТЬ: привет")
eq("закрывающая скобка не остаётся",
   "]" in clean_text("[!] ГОСТЬ: ветер"), False)
eq("имя спикера убирается", clean_text("ВЕДУЩИЙ: скажи"), "скажи")
eq("имя спикера остаётся", clean_text("ВЕДУЩИЙ: скажи", False), "ВЕДУЩИЙ: скажи")
eq("лишние пробелы", clean_text("  много   пробелов  "), "много пробелов")
eq("пустая строка", clean_text(""), "")

# --- служебные подписи не попадают в субтитры ---
ok = eq  # здесь проверки называются eq, как и во всём файле

from srt import SERVICE_LABELS, segments_from_selects
ok("«закадр» считается служебной", "закадр" in SERVICE_LABELS, True)
ok("«перебивка» считается служебной", "перебивка" in SERVICE_LABELS, True)

import csv as _csv
import tempfile as _tf
from pathlib import Path as _P

# --- тайминг субтитров берётся из секвенции, а не из исходника ---
with _tf.TemporaryDirectory() as _td:
    _p = _P(_td) / "selects.csv"
    with open(_p, "w", encoding="utf-8-sig", newline="") as _f:
        _w = _csv.writer(_f)
        # in/out внутри исходника намеренно не подряд: Premiere сжимает паузы
        _w.writerow(["file", "duration", "in", "out", "desc", "text"])
        _w.writerow(["a.mp4", 3.0, 100.0, 103.0, "продюсер", "Рекорд пошёл"])
        _w.writerow(["b.mp4", 2.0, 500.0, 502.0, "закадр", ""])
        _w.writerow(["c.mp4", 4.0, 7.0, 11.0, "ведущий", "И продолжаем"])
    segs = segments_from_selects(_p)
    eq("служебная строка пропущена", len(segs), 2)
    eq("первый субтитр с нуля", round(segs[0]["start"], 2), 0.0)
    # второй идёт сразу за первым: 3 + 2 секунды, а не по времени исходника
    eq("время из секвенции, не из исходника", round(segs[1]["start"], 2), 5.0)
    eq("текст из колонки text", segs[0]["text"], "Рекорд пошёл")

# --- перенос строк ---
# "один два три" ровно 12 символов — влезает в ширину 12
eq("перенос по словам",
   wrap_text("один два три четыре пять шесть", 12),
   "один два три\nчетыре пять\nшесть")
eq("перенос не ломает слова", wrap_text("супердлинное", 5), "супердлинное")

# --- сборка SRT ---
segs = [
    {"start": 0.0, "end": 2.5, "text": "первая"},
    {"start": 2.5, "end": 5.0, "text": "вторая"},
]
srt = build_srt(segs)
lines = srt.split("\n")
eq("нумерация", lines[0], "1")
eq("строка времени", lines[1], "00:00:00,000 --> 00:00:02,500")
eq("текст", lines[2], "первая")
eq("второй блок", lines[4], "2")
eq("пустая строка между блоками", lines[3], "")

# --- мусор не должен попасть в файл ---
junk = build_srt([
    {"start": 0, "end": 1, "text": "норм"},
    {"start": 1, "end": 2, "text": "   "},
    {"start": 5, "end": 4, "text": "перевёрнутое время"},
    {"start": 9, "end": 9.1, "text": "слишком коротко"},
])
jlines = [ln for ln in junk.split("\n") if ln and not ln.isdigit()
          and "-->" not in ln]
eq("пустые тексты пропущены", jlines, ["норм", "перевёрнутое время"])
eq("время исправлено при развороте", "00:00:04,000 --> 00:00:05,000" in junk, True)

# --- одна реплика, разрезанная на куски, не повторяется ---
cut_up = build_srt([
    {"start": 0.0, "end": 5.0, "text": "Рекорд пошёл"},
    {"start": 5.0, "end": 9.0, "text": "Рекорд пошёл"},
    {"start": 9.0, "end": 12.0, "text": "Рекорд пошёл"},
])
eq("разрезанная реплика склеена в одну", cut_up.count("Рекорд пошёл"), 1)
eq("склейка тянется до конца", "00:00:00,000 --> 00:00:12,000" in cut_up, True)
eq("после склейки один номер", cut_up.splitlines()[0], "1")

# --- но если реплики разнесены по времени, это разные реплики ---
apart = build_srt([
    {"start": 0.0, "end": 3.0, "text": "да"},
    {"start": 8.0, "end": 11.0, "text": "да"},
])
eq("одинаковые реплики в разном месте остаются", apart.count("да"), 2)

# --- speech.json без текста не должен молча портить файл ---
# Раньше build_srt выкидывал реплики без текста, на выходе оставался файл
# в 5 байт, а инструмент рапортовал «сохранено, 73 реплик». Проверяем,
# что main() отказывается работать и код возврата ненулевой.
import json as _json
import tempfile as _tf
from srt import main as srt_main

with _tf.TemporaryDirectory() as _td:
    _p = _P(_td)
    (_p / "speech.json").write_text(_json.dumps(
        [{"start": 1.0, "end": 3.0}, {"start": 3.0, "end": 5.0}]),
        encoding="utf-8")
    _out = _p / "out.srt"
    rc = srt_main(["--json", str(_p / "speech.json"), "--out", str(_out)])
    eq("пустой SRT не пишется молча", _out.exists(), False)
    eq("возвращается ошибка", rc != 0, True)

    # с текстом - работает
    (_p / "selects.csv").write_text(
        "file,desc,duration,in,out\n"
        "cam.mp4,\"ВЕДУЩИЙ: привет\",2.0,0,2\n",
        encoding="utf-8")
    rc2 = srt_main(["--selects", str(_p / "selects.csv"), "--out", str(_out)])
    eq("с текстом SRT пишется", rc2 == 0 and _out.is_file(), True)

print()
if fail:
    print(f"SRT FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("SRT OK")
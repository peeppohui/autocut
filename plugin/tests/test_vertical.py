# -*- coding: utf-8 -*-
"""Вертикаль 9:16: кадр, скорость и что уходит в Premiere.

Проверяется без ffmpeg - чистая математика фильтра и проброс
настроек. Сам разрез проверяется сквозным прогоном вручную: он
перекодирует видео и на тестах занимает минуты.
"""
import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src" / "ui"))
import bootstrap  # noqa: F401,F403

import vertical

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


# --- фильтр кадра ---
f = vertical.crop_filter(1080, 1920)
ok("режем по центру под 9:16", "crop=trunc(ih*9/16/2)*2:ih" in f, f)
ok("и растягиваем в 1080x1920", "scale=1080:1920" in f, f)
ok("ширина реза чётная", "trunc(ih*9/16/2)*2" in f, f)
ok("без скорости setpts нет", "setpts" not in f, f)

fast = vertical.crop_filter(1080, 1920, 1.5)
ok("ускорение меняет длительность в setpts", "setpts=0.6666" in fast, fast)
ok("ускорение 1.0 ничего не добавляет",
   "setpts" not in vertical.crop_filter(1080, 1920, 1.0), "")
ok("даже мелкое отклонение уважается буквально",
   "setpts" in vertical.crop_filter(1080, 1920, 1.01), "")
ok("бессмысленная скорость не ломает фильтр",
   vertical.crop_filter(1080, 1920, 0) == f
   and vertical.crop_filter(1080, 1920, -1) == f,
   vertical.crop_filter(1080, 1920, 0))

# --- длительность куска берётся из in/out ---
ok("длительность из in/out",
   vertical.clip_duration({"in": "10", "out": "25", "duration": "99"}) == 15.0,
   str(vertical.clip_duration({"in": "10", "out": "25", "duration": "99"})))
ok("без in/out берётся колонка duration",
   vertical.clip_duration({"duration": "7.5"}) == 7.5,
   str(vertical.clip_duration({"duration": "7.5"})))
ok("мусор в in/out не роняет",
   vertical.clip_duration({"in": "", "out": "", "duration": "3"}) == 3.0,
   str(vertical.clip_duration({"in": "", "out": "", "duration": "3"})))
ok("пустая строка даёт ноль",
   vertical.clip_duration({}) == 0.0, str(vertical.clip_duration({})))

# --- проброс настроек в шаги ---
import steps  # noqa: E402

va = steps.vertical_args("rz/selects.csv", "proj", 1080, 1920, 1.5)
ok("шаг вертикали указывает на selects", "--selects" in va, str(va))
ok("шаг вертикали указывает на выходную папку",
   "--outdir" in va, str(va))
ok("скорость уходит в аргументы",
   "--speed" in va and "1.5" in va, str(va))
ok("размер кадра уходит в аргументы",
   "1080" in va and "1920" in va, str(va))

# --- вертикаль обязана объявить секвенцию вертикальной ---
pa = steps.premiere_args("vert/selects.csv", "proj", width=1080, height=1920)
ok("премьера получает вертикальный кадр",
   "--width" in pa and "1080" in pa and "--height" in pa and "1920" in pa,
   str(pa))
plain = steps.premiere_args("rz/selects.csv", "proj")
ok("без вертикали размер не задаётся", "--width" not in plain, str(plain))

print()
if fail:
    print(f"VERTICAL FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("VERTICAL OK")
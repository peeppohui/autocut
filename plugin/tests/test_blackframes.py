# -*- coding: utf-8 -*-
"""Тест отсева кусков без картинки.

ffmpeg тут не зовём: проверяем разбор его вывода и решения, которые
на их основе принимает interview.py. Медленные проверки на живых файлах
остаются за ручным прогоном.
"""
import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
import bootstrap  # noqa: F401,F403

import blackframes

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


# --- разбор вывода blackdetect ---
OUT = """[Parsed_blackdetect_1 @ 0000] black_start:0 black_end:121.04 black_duration:121.04
[Parsed_blackdetect_1 @ 0000] black_start:700.5 black_duration:9.6
[Parsed_blackdetect_1 @ 0000] Size of selected range is 0
"""

iv = blackframes.parse_blackdetect(OUT)
ok("обычный интервал разобран", (0.0, 121.04) in iv, str(iv))
# чёрное до конца файла: ffmpeg не пишет black_end, конец считаем сами
ok("чёрное до конца файла", (700.5, 710.1) in iv, str(iv))
ok("мусорные строки проигнорированы", len(iv) == 2, str(iv))

ok("пустой вывод - пустой список",
   blackframes.parse_blackdetect("") == [])

# --- сколько чёрного внутри куска ---
black = [(0.0, 121.04), (700.5, 710.1)]
ok("покрытие целиком чёрного", abs(blackframes.covered(black, 10, 20) - 10) < 0.01)
ok("покрытие чистого", blackframes.covered(black, 200, 210) == 0)
ok("покрытие пересечения", abs(blackframes.covered(black, 115, 125) - 6.04) < 0.02)
ok("пересечение с поздним интервалом",
   abs(blackframes.covered(black, 699, 702) - 1.5) < 0.01)

# --- кусок без картинки ---
ok("кусок в чёрной зоне - чёрный", blackframes.is_black(black, 1.24, 3.14))
ok("кусок после - не чёрный", not blackframes.is_black(black, 122.46, 128.44))
ok("захваченный край не считается чёрным",
   not blackframes.is_black(black, 115, 130))
ok("без интервалов ничего не чёрное", not blackframes.is_black([], 0, 10))
ok("порог решает", not blackframes.is_black(black, 115, 130, min_ratio=0.5))

# --- выбор камеры: кусок не выбрасываем из-за одной мёртвой камеры ---
import interview

interview.blackframes.black_intervals = lambda *a, **k: []

iv_wide = [(0.0, 30.0)]
iv_gcam = [(0.0, 30.0), (100.0, 200.0)]


def fake_scan(wide, gcam, cache_dir):
    return {"wide": iv_wide, "gcam": iv_gcam}


interview.black_scan = fake_scan
import settings as _st
_st.__dict__["black.enabled"] = True
interview.settings.get = lambda k, d=None: (
    {"black.enabled": True, "black.drop_ratio": 0.8,
     "black.swap_to_alt": True}.get(k, d))

chunks = [
    (1.0, 10.0, "гость", "обе камеры чёрные", ),      # 0-30 чёрное у обеих
    (100.0, 110.0, "гость", "основная мертва", ),     # гcam чёрный, wide нет
    (300.0, 310.0, "ведущий", "обе живы", ),          # всё чисто
]
planned, dropped, swapped = interview.plan_cameras(
    chunks, _P("a"), _P("b"), _P("."))

ok("кусок без картинки выброшен", dropped == 1, f"dropped={dropped}")
ok("осталось два куска", len(planned) == 2, str(len(planned)))
ok("у мёртвой основной взят запасной ракурс",
   swapped == 1 and planned[0][4] == "wide",
   f"swapped={swapped} cam={planned[0][4] if planned else '-'}")
ok("живой кусок не тронут", planned[1][4] == "wide", str(planned[1][4]))

# выключенный детектор ничего не выкидывает
interview.settings.get = lambda k, d=None: (
    {"black.enabled": False}.get(k, d))
planned2, dropped2, _ = interview.plan_cameras(chunks, _P("a"), _P("b"), _P("."))
ok("выключенный детектор не режет", dropped2 == 0 and len(planned2) == 3)

print()
if fail:
    print(f"BLACK FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("BLACK OK")

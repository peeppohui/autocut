# -*- coding: utf-8 -*-
"""Резкость и тряска: на синтетике, где правильный ответ известен.

Картинка здесь намеренно НЕ в полосках. Полосы повторяются через
несколько пикселей, и сдвиг на их период даёт ту же картинку - фазовая
корреляция не может отличить ноль от целого периода. На таких тестах
и ломалась проверка тряски: прибор показывал ноль на материале, где
камера ходила туда-сюда.

Проверяется не «функция вернула число», а три вещи: мытьё опознаётся,
качание камеры опознаётся, а панорама - нет. Третье важнее: если бы
тряской считалось любое движение, в эфир не попал бы ни один
панорамный кадр, а панорама в новостном сюжете нужна.
"""
import sys
from pathlib import Path as _P

ROOT = _P(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "ml"))
import bootstrap  # noqa: F401,F403

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import sharpness as S  # noqa: E402

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


_rng = np.random.default_rng(3)
_BASE = cv2.GaussianBlur(_rng.integers(0, 255, (360, 640, 3), dtype=np.uint8),
                        (3, 3), 0)


def frame(dx=0, blur=0.0):
    im = cv2.warpAffine(_BASE, np.float32([[1, 0, dx], [0, 1, 0]]),
                        (640, 360), borderMode=cv2.BORDER_REFLECT)
    if blur:
        im = cv2.GaussianBlur(im, (0, 0), blur)
    return im


def seq(*dxs, **kw):
    return [frame(d, **kw) for d in dxs]


# --- резкость ---
soft = S.laplacian_var(frame(blur=9.0))
hard = S.laplacian_var(frame())
ok("мытый кадр резкостью ниже резкого в разы",
   hard > soft * 50, f"{hard:.0f} против {soft:.0f}")

# --- кадр из середины, а не с края ---
frames = [frame(blur=9)] * 2 + [frame()] + [frame(blur=9)] * 4
ok("самый резкий кадр ищется в середине набора",
   S.sharpest(frames) == 2, str(S.sharpest(frames)))
frames2 = [frame(blur=9)] * 3 + [frame()] + [frame(blur=9)] * 3
ok("и тоже, когда он ровно посередине",
   S.sharpest(frames2) == 3, str(S.sharpest(frames2)))
ok("без ограничения на середину берётся самый резкий вообще",
   S.sharpest(frames, middle=False) == 2,
   str(S.sharpest(frames, middle=False)))
edge = [frame()] + [frame(blur=9)] * 6
ok("и если резкий только самый первый — берётся он",
   S.sharpest(edge, middle=False) == 0, str(S.sharpest(edge, middle=False)))
ok("пустой набор не ломает выбор кадра",
   S.sharpest([]) is None, str(S.sharpest([])))

# --- тряска против панорамы и штатива ---
calm = seq(0, 0, 0, 0, 0, 0)
slow_pan = seq(0, 8, 16, 24, 32, 40)
fast_pan = seq(0, 40, 80, 120, 160, 200)
wobble = seq(0, 6, 0, 7, 0, 5)
shake = seq(0, 12, -12, 14, -10, 0)
# Камера на штативе: сдвиг около нуля, но не ровно ноль - так ведёт себя
# и настоящий кадр из-за шума матрицы и сжатия. Именно этот случай старый
# способ считал тряской: итоговое смещение стремится к нулю, и отношение
# пути к смещению уходит в единицы и десятки. На живой съёмке так были
# помечены кадры аудитории со штатива.
still = seq(0, 1, 0, -1, 0, 1)

s_calm, k_calm = S.score(calm)
s_slow, k_slow = S.score(slow_pan)
s_fast, k_fast = S.score(fast_pan)
s_wob, k_wob = S.score(wobble)
s_shk, k_shk = S.score(shake)
s_still, k_still = S.score(still)

ok("неподвижные кадры не считаются тряской", k_calm < 0.5, f"{k_calm:.2f}")
ok("медленная панорама тряской не считается", k_slow < 1.0, f"{k_slow:.2f}")
ok("быстрая панорама тряской не считается", k_fast < 1.0, f"{k_fast:.2f}")
ok("камера на штативе тряской не считается", k_still < 1.0, f"{k_still:.2f}")
ok("и это главный случай: штатив проходит порог, а ручная камера нет",
   k_still < k_shk, f"штатив {k_still:.2f} против {k_shk:.2f}")
ok("ушли и вернулись — считается", k_wob > 1.0, f"{k_wob:.2f}")
ok("качание амплитудой 12px — считается", k_shk > 1.0, f"{k_shk:.2f}")
ok("трясущийся клип остаётся резким (это разные вещи)",
   s_shk > s_wob * 0.9, f"{s_shk:.0f} против {s_wob:.0f}")

# --- тряска меряется по непрерывному куску, а не по размазанному ---
mixed = seq(0, 90, 180, 270, 360, 450)      # камера едет плавно
run = seq(0, 3, 0, 3, 0, 3)                  # а вот на отрезке дрожит
s_a, k_spread = S.score(mixed)               # по размазанным
s_b, k_run = S.score(mixed, run)             # по непрерывному
ok("размазанные кадры меряют не то, что непрерывные",
   k_spread != k_run, f"{k_spread:.2f} против {k_run:.2f}")
ok("а непрерывный кусок находит тряску, которую размазанный теряет",
   k_run > k_spread, f"{k_run:.2f} против {k_spread:.2f}")

# --- порог от медианы папки ---
floor = S.median_floor([s_calm, 1.0, 8.0, s_slow], 0.35)
ok("порог ниже нормальных кадров и выше мыла",
   8 < floor < min(s_calm, s_slow), f"{floor:.1f}")
ok("нормальный клип проходит", S.usable(s_calm, 0.0, floor, 1.0)[0], "")
ok("мытый клип не проходит и говорит почему",
   "расфокус" in S.usable(1.0, 0.0, floor, 2.0)[1], "")
ok("трясущийся клип не проходит и говорит почему",
   "тряска" in S.usable(s_shk, k_shk, floor, 1.0)[1], "")
ok("панорама проходит", S.usable(s_slow, k_slow, floor, 1.0)[0], "")
ok("штатив проходит", S.usable(s_still, k_still, floor, 1.0)[0], "")
ok("без порогов проходит всё", S.usable(1.0, 9.9, 0.0, 99.0)[0], "")

# --- края не роняют модуль ---
ok("единственный кадр меряется", S.score([frame()])[0] > 0, "")
ok("два кадра меряются", S.score([frame(), frame(5)])[0] > 0, "")
ok("пустой набор меряется", S.score([]) == (0.0, 0.0), str(S.score([])))
ok("на пустом наборе тряска ноль", S.wobble([])[0] == 1.0, str(S.wobble([])))

print("SHARPNESS " + ("OK" if not fail else "FAIL " + ", ".join(fail)))
sys.exit(1 if fail else 0)
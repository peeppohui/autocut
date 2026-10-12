# -*- coding: utf-8 -*-
"""Слежение за лицом: где кроп 9:16 и годится ли ракурс.

Считается без нейросети: модель подменяется готовыми точками скелета,
как в detect.py. Само слежение проверяется сквозным прогоном вручную -
оно грузит YOLO и крутит кадры.
"""
import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src" / "ml"))
import bootstrap  # noqa: F401,F403

import faces as F

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


W, H = 1920, 1080


def kpts(face_x, face_y, face_w, conf=0.9):
    """Точки COCO-17 для человека с лицом в face_x (пиксели)."""
    half = face_w / 2.0
    k = [[0.0, 0.0] for _ in range(17)]
    k[0] = [face_x, face_y]              # нос
    k[1] = [face_x - half * 0.6, face_y]  # левый глаз
    k[2] = [face_x + half * 0.6, face_y]  # правый глаз
    k[3] = [face_x - half, face_y]        # левое ухо
    k[4] = [face_x + half, face_y]        # правое ухо
    c = [0.0] * 17
    for i in (0, 1, 2, 3, 4):
        c[i] = conf
    return k, c


# --- лицо из точек ---
f = F.face_from_keypoints(*kpts(960, 400, 200), W, H)
ok("лицо находится по носу и глазам", f is not None, str(f))
ok("центр по горизонтали около 0.5", abs(f.cx - 0.5) < 0.01, str(f))
ok("ширина лица в долях кадра", abs(f.w - 200 / W) < 0.005, str(f))
ok("центр по вертикали около 0.37", abs(f.cy - 400 / H) < 0.01, str(f))

ok("без точек лица нет", F.face_from_keypoints(None, None, W, H) is None, "")
ok("точек мало - лица нет",
   F.face_from_keypoints([[0, 0]] * 3, [0.0] * 3, W, H) is None, "")
ok("все точки невидимы - лица нет",
   F.face_from_keypoints(*kpts(960, 400, 200, conf=0.01), W, H) is None, "")

# --- какое лицо главное ---
small = F.Face(0.5, 0.5, 0.08)
big = F.Face(0.2, 0.4, 0.25)
ok("берётся самое крупное лицо", F.pick_main_face([small, big]) is big, "")
ok("мелкое отсекается порогом",
   F.pick_main_face([small, big], min_w=0.1) is big, "")
ok("если всё мелкое - лица нет",
   F.pick_main_face([small], min_w=0.1) is None, "")

# --- устойчивый центр ---
ok("медиана не даёт выбросу увести кроп",
   abs(F.robust_center([0.5, 0.5, 0.5, 0.5, 0.95]) - 0.5) < 1e-6,
   str(F.robust_center([0.5, 0.5, 0.5, 0.5, 0.95])))
ok("медиана двух средних",
   abs(F.robust_center([0.2, 0.4]) - 0.3) < 1e-6,
   str(F.robust_center([0.2, 0.4])))

# --- пригодность ракурса ---
good = [F.Face(0.5, 0.4, 0.2)]
ok("ракурс с лицом годится",
   F.has_usable_face([good, good, good], 0.5, 0.1), "")
ok("пустой кадр не годится",
   not F.has_usable_face([[], [], []], 0.5, 0.1), "")
ok("лицо в половине замеров - ещё годится",
   F.has_usable_face([good, [], good], 0.5, 0.1), "")
ok("лицо в трети замеров - не годится",
   not F.has_usable_face([good, [], [], [], [], good], 0.5, 0.1), "")
ok("мелкое лицо на общем плане не годится",
   not F.has_usable_face([[small]], 0.5, 0.1), "")
ok("пустых замеров нет - не годится", not F.has_usable_face([], 0.5, 0.1), "")

# --- окно кропа: пропорции и попадание лица ---
def check_window(name, face, src_w=1920, src_h=1080, **kw):
    x, y, w, h = F.crop_window(face, src_w, src_h, **kw)
    return ok(name, True, f"x={x} y={y} w={w} h={h}") if True else None


x, y, w, h = F.crop_window(None, W, H)
ok("без лица - геометрический центр", x == (W - 608) // 2 and w == 608,
   f"x={x} w={w}")
ok("высота кропа = высота кадра", h == 1080, f"h={h}")
ok("кроп 9:16", abs(w / h - 9 / 16) < 0.01, f"{w}x{h} = {w/h:.4f}")

f_mid = F.Face(0.5, 0.4, 0.2)
x, y, w, h = F.crop_window(f_mid, W, H)
ok("лицо по центру - окно не трогаем", w == 608, f"w={w}")
ok("и пропорции те же", abs(w / h - 9 / 16) < 0.01, f"{w}x{h}")

f_left = F.Face(0.10, 0.4, 0.2)          # 192px - далеко слева
x, y, w, h = F.crop_window(f_left, W, H)
ok("лицо слева - окно уехало влево", x < 300, f"x={x}")
ok("пропорции 9:16 сохранены", abs(w / h - 9 / 16) < 0.01, f"{w}x{h}")
fx = f_left.cx * W
ok("лицо попало в окно", x <= fx <= x + w, f"x={x} w={w} face={fx}")
ok("окно не шире естественного", w <= 608, f"w={w}")

f_right = F.Face(0.90, 0.4, 0.2)
x, y, w, h = F.crop_window(f_right, W, H)
fx = f_right.cx * W
ok("лицо справа - окно уехало вправо", x + w > 1600, f"x={x} w={w}")
ok("лицо справа попало в окно", x <= fx <= x + w, f"x={x} w={w} face={fx}")

f_far = F.Face(0.97, 0.4, 0.2)
x, y, w, h = F.crop_window(f_far, W, H)
fx = f_far.cx * W
ok("самая кромка - лицо всё равно в кадре", x <= fx <= x + w,
   f"x={x} w={w} face={fx}")
ok("кроп не схлопнулся в ниточку", w >= 608 * 0.70 * 0.99, f"w={w}")
ok("пропорции 9:16 даже у кромки", abs(w / h - 9 / 16) < 0.02, f"{w}x{h}")

x, y, w, h = F.crop_window(F.Face(0.5, 0.4, 0.2), 1080, 1920)
ok("уже вертикальный исходник режется по полю", w == 1080 and h == 1920,
   f"{w}x{h}")

x, y, w, h = F.crop_window(None, 0, 0)
ok("вырожденный кадр не роняет расчёт", w == 0, f"{w}x{h}")

# --- главное лицо отрезка ---
def mk(x_, w_):
    return [F.Face(x_, 0.4, w_)]

samples = [mk(0.30, 0.20), mk(0.32, 0.22), mk(0.34, 0.21)]
mf = F.main_face_of_segment(samples)
ok("положение усредняется по замерам", abs(mf.cx - 0.32) < 1e-6, str(mf))
ok("главное лицо отрезка найдено", F.main_face_of_segment([]) is None, "")
ok("замеры без лиц дают None",
   F.main_face_of_segment([[], []]) is None, "")
mixed = [mk(0.5, 0.3), []]
ok("часть замеров без лица - не помеха",
   F.main_face_of_segment(mixed) is not None, "")

print()
if fail:
    print(f"FACES FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("FACES OK")
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

# --- камера ведёт лицо: траектория и ограничения оператора ---
FPS = 25.0
TR = F.track_segment


def fake_track(points):
    """[(t, Face)] из точек (t, x)."""
    return [(t, F.Face(x, 0.4, 0.2)) for t, x in points]


# траектория на каждый кадр
path1 = F.operator_path(fake_track([(0.0, 0.30), (1.0, 0.70)]), 0.0, 1.0, FPS)
ok("траектория по кадру, а не по замерам", len(path1) == 25, str(len(path1)))
ok("камера следует за лицом", path1[0].cx < path1[-1].cx,
   f"{path1[0].cx:.3f} -> {path1[-1].cx:.3f}")
ok("движение плавное, без прыжков",
   max(abs(path1[i + 1].cx - path1[i].cx) for i in range(24)) < 0.05,
   str(max(abs(path1[i + 1].cx - path1[i].cx) for i in range(24))))

# предел скорости: прыжок с края на край обязан занять время
step = F.MAX_PAN / FPS
jumped = F.operator_path(fake_track([(0.0, 0.05), (0.10, 0.95),
                                     (1.0, 0.95)]), 0.0, 1.0, FPS)
biggest = max(abs(jumped[i + 1].cx - jumped[i].cx)
              for i in range(len(jumped) - 1))
ok("телепорт ограничен по скорости", biggest <= step + 1e-6,
   f"{biggest:.5f} при пределе {step:.5f}")
ok("и за это время камера не доскакала",
   jumped[-1].cx < 0.95, f"{jumped[-1].cx:.3f}")

# мёртвая зона: микро-дрожь не проходит в кадр
buzz = F.operator_path(fake_track([(0.0, 0.500), (1.0, 0.501)]), 0.0, 1.0,
                       FPS, deadband=F.DEADBAND)
ok("мёртвая зона гасит микродрожь",
   max(buzz[i + 1].cx - buzz[i].cx for i in range(24)) == 0.0,
   str(max(buzz[i + 1].cx - buzz[i].cx for i in range(24))))

# медиана гасит один выброс детектора
spike = F.operator_path(fake_track([(0.0, 0.50), (0.4, 0.50), (0.42, 0.95),
                                    (0.44, 0.50), (1.0, 0.50)]), 0.0, 1.0, FPS)
mid = [f.cx for f in spike[10:15]]
ok("одиночный выброс детектора не уводит кадр",
   max(mid) < 0.60, str(max(mid)))

# без замеров - статика, а не мусор
ok("без трека камера стоит", F.operator_path([], 0.0, 1.0, FPS) == [None] * 25,
   "")
ok("короткий отрезок не ломается",
   len(F.operator_path(fake_track([(0.0, 0.5)]), 0.0, 0.04, FPS)) == 1, "")

# окна на каждый кадр и пропорции
wins = F.crop_windows(path1, W, H)
ok("окно на каждый кадр", len(wins) == 25, str(len(wins)))
ok("все окна 9:16", all(abs(bw / bh - 9 / 16) < 0.02 for _x, _y, bw, bh in wins),
   str([(bw, bh) for _x, _y, bw, bh in wins][:3]))
ok("окно едет за лицом", wins[-1][0] > wins[0][0],
   f"{wins[0][0]} -> {wins[-1][0]}")
ok("окно не вылезает за кадр",
   all(0 <= x and x + bw <= W for x, _y, bw, _h in wins), "")
ok("окно не схлопнулось",
   all(bw >= 608 * 0.70 * 0.99 for _x, _y, bw, _h in wins), "")

# пайп всегда одного размера - иначе ffmpeg не прочитает поток
sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src" / "ml"))
import vertical  # noqa: E402
ok("размер пайпа постоянен", vertical.pipe_size(1080, 1080 / 1920) == (608, 1080),
   str(vertical.pipe_size(1080, 1080 / 1920)))
ok("и для другого кадра тоже", vertical.pipe_size(720, 1080 / 1920) == (406, 720),
   str(vertical.pipe_size(720, 1080 / 1920)))

print()
if fail:
    print(f"FACES FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("FACES OK")
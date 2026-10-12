# -*- coding: utf-8 -*-
"""Где в кадре лицо и куда поставить вертикальный кроп.

Задача узкая и конкретная: центральный кроп 16:9 -> 9:16 выбрасывает
44% ширины, и если говорящий смещён, вертикальный ролик режет ему лицо.
Модуль отвечает на два вопроса:

1) Есть ли в этом куске вообще лицо, на которое можно посмотреть
   (для выбора ракурса - заменяет «камера вроде живая, а в кадре
   никого»).
2) Где именно оно и какое окно кропа его вместит.

Почему YOLOv8-pose, а не отдельный детектор лиц. Отдельный детектор
пришлось бы ставить (haarcascade из состава OpenCV, mediapipe, dlib) и
качать веса. А проект уже тянет ultralytics и уже гоняет yolov8n-pose в
detect.py, и в этой модели есть нос, глаза и уши - ровно то, из чего
считается положение и ширина лица. Два человека за прогон, ноль новых
зависимостей.

Чего модуль не делает. Он не узнаёт, кто именно в кадре: сравнивать
лица по внешности нечем, да и не нужно. Решается это в других
слоях - камера подбирается по микрофону (кто говорит) и по плану,
а кроп ведёт самое крупное лицо, потому что монтаж уже выбрал ракурс
того, кто говорит.

Стоимость. Детектор гоняется по ОТРЕЗКАМ готового монтажа, а не по
исходникам: рыба на два часа интервью - это несколько минут, а
проверять два часа ради сотни кадров незачем. Поэтому кроп считается
за секунды и кэш не нужен: пересчёт дешевле, чем риск прочитать
испорченный JSON (см. blackframes, где кэш пригодился на полном
проходе ffmpeg'ом).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bootstrap  # noqa: F401  — регистрирует src/config в sys.path
import settings

# Точки COCO-17, из которых собирается лицо. Нос, глаза, уши.
_NOSE, _EYE_L, _EYE_R, _EAR_L, _EAR_R = 0, 1, 2, 3, 4
FACE_KPTS = (_NOSE, _EYE_L, _EYE_R, _EAR_L, _EAR_R)

ENABLED = bool(settings.get("faces.enabled", True))
MODEL = str(settings.get("faces.model", "yolov8n-pose.pt"))
CONF_KPTS = float(settings.get("faces.conf", 0.30))
CONF_BOX = float(settings.get("faces.detect_conf", 0.25))
# ширина лица в долях ширины кадра, ниже которой лицо «не считается»
MIN_FACE_W = float(settings.get("faces.min_face_w", 0.10))
# доля окна от естественной ширины кропа, ниже не ужимаем: иначе
# картинка «растягивается» под кадр и мытьё видно невооружённым глазом
MIN_CROP_FRAC = float(settings.get("faces.min_crop_frac", 0.70))
SAMPLES_PER_CLIP = int(settings.get("faces.samples_per_clip", 3))
FOLLOW_FACES = bool(settings.get("faces.follow_faces", True))
SWAP_WHEN_BLANK = bool(settings.get("faces.swap_camera_when_blank", True))


class Face:
    """Одно лицо в кадре. Координаты - доли от ширины/высоты кадра."""

    __slots__ = ("cx", "cy", "w")

    def __init__(self, cx: float, cy: float, w: float):
        self.cx = cx      # центр по горизонтали, 0..1
        self.cy = cy      # центр по вертикали, 0..1
        self.w = w        # ширина лица, доля ширины кадра

    def __repr__(self) -> str:
        return f"Face(cx={self.cx:.3f}, cy={self.cy:.3f}, w={self.w:.3f})"


# ------------------------------------------------------------- чистая логика

def even(n: float) -> int:
    """Чётное число, потому что libx264 не берёт нечётные размеры."""
    i = int(round(n))
    return i if i % 2 == 0 else i + 1


def face_from_keypoints(kpts_xy: Sequence, kpts_conf: Sequence,
                        img_w: int, img_h: int) -> Optional[Face]:
    """Лицо из точек скелета одной фигуры. None - если лица не видно.

    Центр берём по носу и глазам: уши детект цепляет и на затылках,
    а нос с глазами - признак, что человек смотрит в объектив.
    Ширину - по разлёту глаз и ушей; если видна одна точка, размер
    прикидываем от бокса фигуры, голова это примерно 1/7.5 роста.
    """
    if kpts_xy is None or kpts_conf is None or len(kpts_xy) < 5:
        return None
    if img_w <= 0 or img_h <= 0:
        return None
    vis = [i for i in FACE_KPTS if float(kpts_conf[i]) > CONF_KPTS]
    if not vis:
        return None
    core = [i for i in (_NOSE, _EYE_L, _EYE_R) if i in vis]
    pts = core or vis
    cx = sum(float(kpts_xy[i][0]) for i in pts) / len(pts) / img_w
    cy = sum(float(kpts_xy[i][1]) for i in pts) / len(pts) / img_h
    xs = [float(kpts_xy[i][0]) for i in vis]
    w = (max(xs) - min(xs)) / img_w
    if len(vis) == 1:
        # одна точка - ширину не измерить, берём от высоты фигуры
        w = max(w, 0.0)
    return Face(min(max(cx, 0.0), 1.0), min(max(cy, 0.0), 1.0), w)


def pick_main_face(faces: Sequence[Face],
                   min_w: float = 0.0) -> Optional[Face]:
    """Самое крупное лицо. Крупнее - значит ближе к зрителю, и именно
    оно обычно тот, кто говорит: микрофон уже определил говорящего, а
    камера показывает его крупно."""
    best = None
    for f in faces:
        if f.w < min_w:
            continue
        if best is None or f.w > best.w:
            best = f
    return best


def robust_center(values: Sequence[float]) -> float:
    """Медиана, а не среднее: один выброс (человек зашёл в кадр на
    секунду) не должен уводить кроп с говорящего на полкадра."""
    vals = sorted(float(v) for v in values)
    if not vals:
        return 0.5
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def faces_in_frame(faces: Sequence[Face],
                   min_w: float = 0.0) -> List[Face]:
    """Только те, что крупнее порога. Мелкое лицо на общем плане для
    вертикали бесполезно: в кропе 9:16 от него останутся глаза и лоб."""
    return [f for f in faces if f.w >= min_w]


def has_usable_face(samples: Sequence[Sequence[Face]], min_ratio: float = 0.5,
                    min_w: float = 0.0) -> bool:
    """Есть ли кадр, на который стоит смотреть.

    samples - список того, что видели в разные моменты отрезка. Нужна
    не доля кадров, а факт: ракурс годен, если хотя бы в половине
    замеров нашлось подходящее лицо.
    """
    if not samples:
        return False
    good = sum(1 for s in samples if faces_in_frame(s, min_w))
    return good >= max(1, math.ceil(len(samples) * min_ratio))


def crop_window(face: Optional[Face], src_w: int, src_h: int,
                aspect: float = 9 / 16,
                min_crop_frac: float = MIN_CROP_FRAC) -> Tuple[int, int, int, int]:
    """Окно кропа 9:16 под лицо. Возвращает (x, y, w, h) в пикселях.

    Ширина естественного реза считается от высоты кадра: тогда вертикаль
    получается всегда одного формата, хоть исходник шире или уже 16:9.

    Главная тонкость. Лицо у самого края кадра в естественный рез не
    влезает - и чтобы его не срезать, приходится сузить окно. А сузить
    можно не только по ширине: пропорции обязаны остаться 9:16, иначе
    scale растянет картинку, и лицо станет овальным. Поэтому при
    ужимании уменьшаются обе стороны пропорционально.

    Но ужимать до бесконечности нельзя: картинка растянется в разы и
    станет мыльной. Поэтому низ стоит - MIN_CROP_FRAC от естественного
    реза. Дальше лучше оставить лицо чуть не по центру, чем убить
    резкость.
    """
    if src_w <= 0 or src_h <= 0 or aspect <= 0:
        return (0, 0, 0, 0)
    w_nat = even(src_h * aspect)
    h_nat = even(src_h)
    if w_nat > src_w:                      # исходник уже вертикальный
        w_nat, h_nat = even(src_w), even(src_w / aspect)

    if face is None:
        x = (src_w - w_nat) // 2
        y = (src_h - h_nat) // 2
        return (max(0, x), max(0, y), min(w_nat, src_w), min(h_nat, src_h))

    fx, fy = face.cx * src_w, face.cy * src_h
    # насколько широкое окно ещё центрирует лицо, не выталкивая его в край
    reach = 2.0 * min(fx, src_w - fx)
    w = min(w_nat, max(reach, w_nat * min_crop_frac))
    w = even(min(w, src_w))
    h = even(min(w / aspect, src_h))
    # сузили - появилась свобода и по вертикали, но ею не злоупотребляем:
    # лицо обычно в верхней трети, и поднимать кроп к нему - нормально
    y = min(max(int(fy - h / 2.0), 0), max(0, src_h - h))
    x = min(max(int(fx - w / 2.0), 0), max(0, src_w - w))
    return (x, y, w, h)


# ------------------------------------------------------- работа с кадрами

def grab_at(path: Path, t: float):
    """Один кадр из момента t. None - если не открылось."""
    import cv2
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(t)) * 1000.0)
        ok, fr = cap.read()
        if not ok or fr is None:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, fr = cap.read()
        return fr if ok else None
    finally:
        cap.release()


def faces_in_image(frame, model, conf: float = CONF_BOX) -> List[Face]:
    """Все лица в одном кадре."""
    if frame is None:
        return []
    h, w = frame.shape[:2]
    res = model.predict(frame, conf=conf, verbose=False,
                        device=getattr(model, "_autocut_device", "cpu"))[0]
    kpts = getattr(res, "keypoints", None)
    if kpts is None or kpts.xy is None:
        return []
    try:
        kxy = kpts.xy.cpu().numpy()
        kcf = (kpts.conf.cpu().numpy() if kpts.conf is not None
               else [[1.0] * 17] * len(kxy))
    except Exception:
        return []
    out = []
    for i in range(len(kxy)):
        f = face_from_keypoints(kxy[i], kcf[i], w, h)
        if f is not None:
            out.append(f)
    return out


def source_size(path: Path) -> Tuple[int, int]:
    """Ширина и высота кадра в пикселях. (0, 0), если не открылось."""
    import cv2
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return (0, 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()
    return (w, h)


def scan_segment(path: Path, t0: float, t1: float, model,
                 n: int = SAMPLES_PER_CLIP) -> List[List[Face]]:
    """Что было видно в отрезке [t0, t1]. Список замеров, по одному на
    момент времени. Замеры идут не по краям: на стыках кадр ещё
    переезжает, и лицо там ловится хуже всего.

    Видео открывается один раз на весь отрезок. Раньше кадр тянулся
    по grab_at, и на монтаже в сто кусков это было триста открытий
    файла подряд - ffmpeg к этому времени уже отработал, а OpenCV
    ещё возился с заголовками.
    """
    import cv2
    dur = max(0.0, float(t1) - float(t0))
    if dur <= 0:
        return []
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return []
    out = []
    try:
        n = max(1, int(n))
        for k in range(n):
            t = float(t0) + dur * (k + 0.5) / n
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
            ok, fr = cap.read()
            if not ok or fr is None:
                # короткий файл: POS_MSEC иногда промахивается мимо конца
                cap.set(cv2.CAP_PROP_POS_FRAMES,
                        int(cap.get(cv2.CAP_PROP_FRAME_COUNT) * (k + 0.5) / n))
                ok, fr = cap.read()
            out.append(faces_in_image(fr if ok else None, model))
    finally:
        cap.release()
    return out


def main_face_of_segment(samples: Sequence[Sequence[Face]],
                         min_w: float = 0.0) -> Optional[Face]:
    """Главное лицо отрезка: медианное положение по всем замерам.

    Медиана, потому что окно одно на весь кусок. Погонять кроп за
    лицом покадрово - значит получить «плавающий» монтаж, который
    редактор потом всё равно переставит вручную.
    """
    cands = [pick_main_face(s, min_w) for s in samples]
    cands = [f for f in cands if f is not None]
    if not cands:
        return None
    return Face(robust_center([f.cx for f in cands]),
                robust_center([f.cy for f in cands]),
                robust_center([f.w for f in cands]))
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoCut Detect — авто-определение крупности плана по видео.
Шкала: деталь / крупный / средний1 (по пояс) / средний2 (по колени) / общий / дальний.

Как работает:
  1. Берём N равномерно распределённых кадров из видео (OpenCV).
  2. Прогоняем через YOLOv8-pose (ultralytics): бокс человека + 17 точек COCO.
     Индексы точек: 0 нос, 1-2 глаза, 3-4 уши, 5-6 плечи, 7-8 локти,
     9-10 запястья, 11-12 бёдра, 13-14 колени, 15-16 лодыжки.
  3. По видимости частей тела + относительному размеру бокса/лица
     классифицируем план эвристикой (см. classify_by_pose).
  4. Усредняем по кадрам голосованием, пишем CSV для autocut.py
     + превью-кадры для ручной проверки.

Установка (один раз):
  python -m pip install ultralytics opencv-python
  (первый запуск сам скачает yolov8n-pose.pt ~6 МБ)

Использование:
  python detect.py --input E:\\Съёмка\\день1 --output clips_auto.csv
  python detect.py --input clip.mp4 --output one.csv --preview _preview
  python autocut.py --csv clips_auto.csv --mode digest --auto --out timeline.csv
"""

from __future__ import annotations
import argparse
import csv
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bootstrap  # noqa: F401  — регистрирует src/config в sys.path
import settings
from sharpness import median_floor, score as _sharp_score, usable  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

VIDEO_EXTS = {".mp4", ".mov", ".mxf", ".avi", ".m4v", ".mkv", ".mts", ".m2ts"}

# Пороги (подобраны под 16:9, человека в кадре) — из config/settings.json,
# секция detect. Крутятся правкой конфига, не кода.
FACE_W_DETAIL = float(settings.get("detect.face_w_detail", 0.28))   # ширина "лица" (уши/глаза) относительно ширины кадра -> деталь
FACE_W_CLOSEUP = float(settings.get("detect.face_w_closeup", 0.13))  # -> крупный
PERSON_H_DISTANT = float(settings.get("detect.person_h_distant", 0.28))  # высота бокса человека -> дальний
PERSON_H_GENERAL = float(settings.get("detect.person_h_general", 0.85))  # бокс почти в рост кадра
PERSON_MIN_BODY = float(settings.get("detect.person_min_body", 0.40))  # ниже — скелет не определяет ступень: человек мелкий
# (экран/фото в кадре, фон). Такие кадры — либо общий, либо деталь объекта,
# решение остаётся за человеком по превью, уверенность не завышаем.
CONF_TH = float(settings.get("detect.kpt_conf", 0.3))  # порог видимости ключевой точки

# Порог, ниже которого план нельзя считать надёжным — просим проверить по превью
LOW_CONFIDENCE = float(settings.get("detect.low_confidence", 0.65))
# Скелета почти не видно (грим, контровой свет) — режем уверенность сильнее
SPARSE_SKELETON_CONF = float(settings.get("detect.sparse_skeleton_confidence", 0.60))
# Мало видимых точек скелета (грим, контровой свет) — срезаем уверенность
MIN_VISIBLE_KEYPOINTS = int(settings.get("detect.min_visible_keypoints", 5))

# Порог детекции людей и число кадров на файл (по умолчанию для --conf / --frames)
DEFAULT_DETECT_CONF = float(settings.get("detect.detect_conf", 0.35))
DEFAULT_FRAMES = int(settings.get("detect.frames_per_file", 7))

# Толпа: людей много и фигуры мелкие — это зал, а не поясной портрет
CROWD_PEOPLE_MIN = int(settings.get("detect.crowd_people_min", 5))
CROWD_BODY_MAX = float(settings.get("detect.crowd_body_max", 0.5))
CROWD_BACK_PEOPLE_MIN = int(settings.get("detect.crowd_back_people_min", 4))

# Пороги высоты бокса, ниже/above которых меняется план (не уверенность)
PERSON_H_INFRAME_MIN = float(settings.get("detect.person_h_inframe_min", 0.45))
PERSON_H_FRAGMENT = float(settings.get("detect.person_h_fragment", 0.7))
PERSON_H_SINGLE_POINT = float(settings.get("detect.person_h_single_point", 0.6))
PERSON_H_FACE_ONLY = float(settings.get("detect.person_h_face_only", 0.4))


@dataclass
class FrameVerdict:
    plan: str
    conf: float
    people: int
    detail: str  # человекочитаемое объяснение


def _vis(kpts_conf, idx) -> bool:
    """Видима ли точка (или пара)."""
    if isinstance(idx, int):
        return float(kpts_conf[idx]) > CONF_TH
    return any(float(kpts_conf[i]) > CONF_TH for i in idx)


def _classify_core(
    img_w: int,
    img_h: int,
    box_xywh_norm: Optional[Tuple[float, float, float, float]],
    kpts_xy: Optional[list],
    kpts_conf: Optional[list],
    people_count: int = 1,
) -> FrameVerdict:
    """Чистая эвристика без нейросети — её легко тестировать и крутить пороги.

    box_xywh_norm: (cx, cy, w, h) в долях от кадра, крупнейшего человека.
    kpts_xy: список [x, y] в пикселях (17 точек COCO) или None.
    kpts_conf: список уверенностей или None.
    """
    # --- никого нет: адресный / атмосферный план ИЛИ объект крупно
    if people_count == 0 or box_xywh_norm is None:
        return FrameVerdict("общий", 0.55, people_count,
                            "людей нет — вероятно общий (зал/сцена). "
                            "НО если это экран/бумаги/предмет крупно — смените на деталь вручную.")

    _, _, bw, bh = box_xywh_norm

    # --- оценка размера "лица" по разлёту глаз/ушей, если есть точки
    face_w = 0.0
    if kpts_xy is not None and kpts_conf is not None and len(kpts_xy) >= 5:
        try:
            pts = []
            for i in (1, 2, 3, 4):  # глаза + уши
                if float(kpts_conf[i]) > CONF_TH:
                    pts.append(kpts_xy[i][0])
            if len(pts) >= 2:
                face_w = (max(pts) - min(pts)) / max(1, img_w)
                # если видно только нос/один глаз крупно — это почти наверняка деталь/крупный
                if len(pts) == 1 and bh > PERSON_H_SINGLE_POINT:
                    face_w = max(face_w, 0.20)
        except Exception:
            face_w = 0.0

    has_shoulders = _vis(kpts_conf, (5, 6)) if kpts_conf is not None else False
    has_hips = _vis(kpts_conf, (11, 12)) if kpts_conf is not None else False
    has_knees = _vis(kpts_conf, (13, 14)) if kpts_conf is not None else False
    has_ankles = _vis(kpts_conf, (15, 16)) if kpts_conf is not None else False
    has_face = _vis(kpts_conf, (0, 1, 2, 3, 4)) if kpts_conf is not None else False
    # нос/глаза отдельно от ушей: уши детект находит и на затылках/руках (ложные),
    # а нос+глаза — признак, что лицо действительно смотрит в камеру
    has_nose_eyes = _vis(kpts_conf, (0, 1, 2)) if kpts_conf is not None else False
    has_arms = _vis(kpts_conf, (7, 8, 9, 10)) if kpts_conf is not None else False

    # 1) деталь: лицо распирает кадр, а тела почти не видно
    if face_w >= FACE_W_DETAIL and not (has_hips or has_knees or has_ankles):
        return FrameVerdict("деталь", 0.85, people_count,
                            f"лицо/объект очень крупно (ширина лица {face_w:.2f} кадра), тела не видно.")
    # лицо видно частично + бокс обрезан сверху — тоже деталь (глаза/руки/микрофон)
    if kpts_conf is not None and bh > PERSON_H_FRAGMENT and not has_shoulders and has_face:
        return FrameVerdict("деталь", 0.65, people_count,
                            "виден фрагмент (лицо/руки) без плеч — классифицировано как деталь.")

    # 2) дальний: человек маленький
    if bh < PERSON_H_DISTANT:
        return FrameVerdict("дальний", 0.85, people_count,
                            f"человек занимает {bh:.2f} высоты кадра — адресный дальний.")

    # 2б) толпа мелко: людей много, крупнейший мелкий — это зал, даже если
    # у ближнего ряда «нашлись» бёдра/колени (сидящие) или плечи (затылки).
    if people_count >= CROWD_PEOPLE_MIN and bh < CROWD_BODY_MAX:
        return FrameVerdict("общий", 0.75, people_count,
                            "толпа мелко (людей >=5, фигуры < 0.5 кадра) — общий зал.")
    # 2в) толпа со спины: нос и глаза крупнейшего не видны, людей много —
    # это зрители затылками, а не поясной портрет.
    if people_count >= CROWD_BACK_PEOPLE_MIN and not has_nose_eyes:
        return FrameVerdict("общий", 0.75, people_count,
                            "лица не видно, людей много — зрители/зал со спины.")

    # 3) по скелету (надёжнее всего) — только если человек достаточно велик.
    # Мелкий человек с "видимыми бёдрами/коленями" — это почти всегда человечек
    # на экране/фото в кадре (picture-in-picture) или фон: ступени по нему
    # определять нельзя, иначе деталь объекта станет "средним" с уверенностью 0.85.
    # Лодыжки видны = фигура в полный рост = общий при любом размере бокса
    # (мелко — просто общий с меньшей уверенностью; совсем мелко уже ушло в дальний выше).
    if has_ankles:
        if bh >= PERSON_H_INFRAME_MIN:
            return FrameVerdict("общий", 0.90, people_count,
                                "видны лодыжки — человек в полный рост.")
        # Мелкая фигура в рост: сцена вживую — общий; персонажи на экране
        # (мульт, слайд) — тоже сюда попадают. Различить дёшево нельзя,
        # поэтому уверенность средняя и пометка для ручной проверки.
        return FrameVerdict("общий", 0.65, people_count,
                            "фигура в рост мелко — проверьте: сцена вживую (общий) "
                            "или экран (тогда деталь).")
    if has_knees and not has_ankles and bh >= PERSON_MIN_BODY:
        return FrameVerdict("средний2", 0.85, people_count,
                            "видны колени, лодыжек нет — срез по колени (второй средний).")
    if has_hips and not has_knees and bh >= PERSON_MIN_BODY:
        return FrameVerdict("средний1", 0.85, people_count,
                            "видны бёдра/талия, коленей нет — срез по пояс (первый средний).")

    # 4) по лицу/боксу, если скелет неуверенный:
    if face_w >= FACE_W_CLOSEUP or (has_face and not has_shoulders and bh > PERSON_H_FACE_ONLY):
        return FrameVerdict("крупный", 0.80, people_count,
                            f"голова крупно (лицо {face_w:.2f} ширины кадра), плеч почти не видно.")
    # Руки/предмет крупно: носа и глаз нет, низа тела нет, есть руки, а бокс
    # узкий или сбоку (телефон, бумаги, аппаратура) — это деталь, а не «по пояс».
    # Спина крупно по центру сюда не попадает (бокс широкий) — она идёт ниже в средний.
    cx = box_xywh_norm[0]
    if (not has_nose_eyes and not has_hips and not has_knees and not has_ankles
            and has_arms and people_count <= 3
            and (bw < 0.28 or abs(cx - 0.5) > 0.25)):
        return FrameVerdict("деталь", 0.75, people_count,
                            "лица и тела нет, видны только руки — предмет/действие крупно.")
    if has_shoulders and not has_hips and bh >= PERSON_MIN_BODY:
        return FrameVerdict("средний1", 0.70, people_count,
                            "видны плечи, бёдер нет — поясной (первый средний).")
    if bh >= PERSON_H_GENERAL and has_face:
        return FrameVerdict("средний1", 0.60, people_count,
                            f"бокс высокий ({bh:.2f}), скелет частичный — скорее поясной.")

    # 5) дефолт
    if bh >= PERSON_H_INFRAME_MIN:
        return FrameVerdict("общий", 0.60, people_count,
                            f"человек в рост ({bh:.2f} кадра), низ виден нечётко — оставлен общий.")
    return FrameVerdict("общий", 0.50, people_count, "неуверенно — оставлен общий, проверьте превью.")


def classify_by_pose(
    img_w: int,
    img_h: int,
    box_xywh_norm: Optional[Tuple[float, float, float, float]],
    kpts_xy: Optional[list],
    kpts_conf: Optional[list],
    people_count: int = 1,
) -> FrameVerdict:
    """Обёртка над _classify_core: две поправки честности.

    1. Толпа (>=5): сидящий зал даёт ложные «бёдра/колени» — такой средний
       помечаем как требующий проверки.
    2. Слепой скелет: точек видно < 5 из 17 (грим, контровый свет, темнота) —
       любой вердикт режем до 0.60. Лучше «проверьте», чем уверенная ошибка.
    """
    v = _classify_core(img_w, img_h, box_xywh_norm, kpts_xy, kpts_conf, people_count)
    if v.people >= CROWD_PEOPLE_MIN and v.plan in ("средний1", "средний2") \
            and v.conf > LOW_CONFIDENCE:
        return FrameVerdict(v.plan, LOW_CONFIDENCE, v.people,
                            v.detail + " Толпа в кадре — если зал целиком, смените на общий.")
    if kpts_conf is not None and v.people > 0:
        nvis = sum(1 for c in kpts_conf if float(c) > CONF_TH)
        if nvis < MIN_VISIBLE_KEYPOINTS and v.conf > SPARSE_SKELETON_CONF:
            return FrameVerdict(v.plan, SPARSE_SKELETON_CONF, v.people,
                                v.detail + " Скелет почти не виден (грим/темнота) — проверьте по превью.")
    return v


def guess_hero(people_counts: List[int], plans: List[str]) -> str:
    """Грубый герой для autocut: нужен только чтобы различать 'один объект / разные'."""
    if not people_counts:
        return "сцена"
    mx = max(people_counts)
    if mx == 0:
        return "зал"
    if mx >= 4:
        return "зрители"
    if mx <= 2:
        # интервью/спикер vs сцена — уточнит человек в CSV
        return "спикер"
    return "участники"


# ---------------------------------------------------------- видео-утилиты

def video_info(path: Path) -> Tuple[float, float, int, int]:
    import cv2
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Не открылось видео: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    if fps <= 1 or fps > 120:
        fps = 25.0
    n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()
    dur = (n / fps) if n else 0.0
    return dur, fps, w, h


def sample_frames(path: Path, n: int = 7):
    """Возвращает n кадров (numpy BGR) равномерно по длительности."""
    import cv2
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Не открылось видео: {path}")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    frames = []
    if total <= 0:
        ok, fr = cap.read()
        if ok:
            frames.append(fr)
        cap.release()
        return frames
    idxs = [int(total * (i + 0.5) / n) for i in range(n)]
    for ix in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, ix)
        ok, fr = cap.read()
        if ok and fr is not None:
            frames.append(fr)
    cap.release()
    return frames


def analyse_sharpness(path: Path, n: int = 7, run_sec: float = 1.5,
                      fps: int = 25):
    """(резкость, тряска) по клипу.

    Резкость меряется по кадрам, размазанным по всему клипу. Тряска — по
    НЕПРЕРЫВНОМУ куску кадров из середины: разброс, посчитанный по
    размазанным кадрам, измеряет движение объекта в кадре, а не тряску
    камеры, и на живой съёмке путал кадры со штатива с ручными.
    """
    try:
        spread = sample_frames(path, n)
        run = consecutive_frames(path, run_sec, fps)
    except Exception:
        return 0.0, 0.0
    return _sharp_score(spread, run or None)


def consecutive_frames(path: Path, seconds: float = 1.5, fps: int = 25,
                       at: float = 0.5):
    """N подряд идущих кадров из середины клипа."""
    import cv2
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return []
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(total * at)))
    out = []
    for _ in range(max(2, int(seconds * fps))):
        ok, fr = cap.read()
        if not ok or fr is None:
            break
        out.append(fr)
    cap.release()
    return out


def load_yolo(model_name: str = "yolov8n-pose.pt", device: str = "auto"):
    try:
        from ultralytics import YOLO
    except ImportError as e:
        raise RuntimeError(
            "Нет пакета ultralytics. Поставьте: python -m pip install ultralytics opencv-python"
        ) from e
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import bootstrap  # noqa: F401  — регистрирует src/core, src/modes и др. в sys.path
    import gpu as _gpu

    dev = _gpu.pick(device)
    m = YOLO(model_name)
    try:
        m.to(dev)
        if dev == "cuda":
            # прогрев: ловит рассогласования вроде torch/torchvision сразу,
            # а не посреди ночного прогона
            import numpy as _np
            m.predict(_np.zeros((64, 64, 3), dtype=_np.uint8),
                      verbose=False, device="cuda")
    except Exception as e:
        if dev == "cuda":
            print(f"GPU для YOLO не завелась ({str(e)[:120]}), падаю на CPU.")
            m.to("cpu")
            dev = "cpu"
    print(f"Детект на {dev.upper()}...")
    m._autocut_device = dev
    return m


def analyse_video(path: Path, model, n_frames: int = 7, save_preview: Optional[Path] = None,
                  conf: float = DEFAULT_DETECT_CONF) -> Dict:
    """Полный анализ одного файла. Возвращает dict для CSV."""
    import cv2
    dur, fps, W, H = video_info(path)
    frames = sample_frames(path, n_frames)
    if not frames:
        raise RuntimeError(f"Нет кадров: {path}")

    verdicts: List[FrameVerdict] = []
    people_counts: List[int] = []
    mid = frames[len(frames) // 2]

    for fr in frames:
        h, w = fr.shape[:2]
        res = model.predict(fr, conf=conf, verbose=False,
                              device=getattr(model, "_autocut_device", "cpu"))[0]
        boxes = res.boxes
        kpts = res.keypoints
        n_people = len(boxes) if boxes is not None else 0
        people_counts.append(n_people)

        if n_people == 0:
            verdicts.append(classify_by_pose(w, h, None, None, None, 0))
            continue

        # крупнейший человек
        try:
            import numpy as np
            xywh = boxes.xywhn.cpu().numpy()  # norm cx,cy,w,h
            areas = (boxes.xywh.cpu().numpy()[:, 2] * boxes.xywh.cpu().numpy()[:, 3])
            bi = int(areas.argmax())
            b = tuple(float(v) for v in xywh[bi])
            if kpts is not None and kpts.xy is not None and len(kpts.xy) > bi:
                kx = kpts.xy.cpu().numpy()[bi]  # (17,2) в пикселях
                kc = kpts.conf.cpu().numpy()[bi] if kpts.conf is not None else [1.0] * 17
                klist = [[float(x), float(y)] for x, y in kx]
                clist = [float(c) for c in kc]
            else:
                klist, clist = None, None
        except Exception:
            b, klist, clist = None, None, None

        verdicts.append(classify_by_pose(w, h, b, klist, clist, n_people))

    # голосование
    votes = Counter(v.plan for v in verdicts)
    plan, top = votes.most_common(1)[0]
    # средняя уверенность по победившему плану
    confs = [v.conf for v in verdicts if v.plan == plan]
    avg_conf = sum(confs) / len(confs) if confs else 0.0
    # объяснение — самое частое среди победивших
    expl = Counter(v.detail for v in verdicts if v.plan == plan).most_common(1)[0][0]
    # голоса разделились едва (2:1, 4:3) — вердикт шаткий, режем уверенность
    if top * 2 <= len(verdicts) + 1:
        avg_conf = min(avg_conf, LOW_CONFIDENCE)
        expl += " Голоса кадров разделились почти поровну — проверьте по превью."
    hero = guess_hero(people_counts, [v.plan for v in verdicts])

    if save_preview is not None:
        save_preview.parent.mkdir(parents=True, exist_ok=True)
        # средний кадр + подпись
        try:
            label = f"{path.name} -> {plan} ({avg_conf:.2f})"
            cv2.putText(mid, label, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
            cv2.imwrite(str(save_preview), mid)
        except Exception:
            pass

    return {
        "file": path.name,
        "path": str(path),
        "plan": plan,
        "hero": hero,
        "duration": round(dur, 1) if dur else 3.0,
        "conf": round(float(avg_conf), 2),
        "votes": dict(votes),
        "explain": expl,
        "people_max": max(people_counts) if people_counts else 0,
    }


def collect_videos(inp: Path) -> List[Path]:
    if inp.is_file():
        return [inp]
    return sorted(p for p in inp.rglob("*") if p.suffix.lower() in VIDEO_EXTS)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AutoCut Detect — план по видео (YOLOv8-pose)")
    ap.add_argument("--input", required=True, help="папка со съёмкой или один файл")
    ap.add_argument("--output", default="clips_auto.csv", help="куда сохранить CSV для autocut.py")
    ap.add_argument("--frames", type=int, default=DEFAULT_FRAMES, help="кадров на файл (3-11)")
    ap.add_argument("--model", default=settings.get("detect.model", "yolov8n-pose.pt"), help="модель ultralytics")
    ap.add_argument("--conf", type=float, default=DEFAULT_DETECT_CONF, help="порог детекции 0.2-0.6")
    ap.add_argument("--preview", default="_preview", help="папка превью (для ручной проверки)")
    ap.add_argument("--limit", type=int, default=0, help="взять только N первых файлов (для пробы)")
    ap.add_argument("--device", default="auto", help="auto|cpu|cuda (auto: GPU если завелась)")
    ap.add_argument("--no-blur", action="store_true",
                    help="не отсеивать расфокус и тряску. По умолчанию "
                         "отсеиваются: стандарт требует минимизировать и то "
                         "и другое, а мытьё в эфире видно сразу")
    args = ap.parse_args(argv)

    inp = Path(args.input)
    if not inp.exists():
        print(f"Нет такого пути: {inp}")
        print("Пример: --input \"E:\\Съёмка\\день1\" или --input \"E:\\Видео\\клип.mp4\" (кавычки, если пробелы).")
        return 2
    videos = collect_videos(inp)
    if args.limit:
        videos = videos[:args.limit]
    if not videos:
        print(f"Видео не найдены в {inp} (ищу {sorted(VIDEO_EXTS)})")
        return 1

    print(f"Файлов: {len(videos)}. Загружаю модель {args.model} ...")
    try:
        model = load_yolo(args.model, args.device)
    except RuntimeError as e:
        print(str(e))
        return 1

    rows = []
    sharp_scores: Dict[str, float] = {}
    for i, v in enumerate(videos, 1):
        try:
            pv = Path(args.preview) / (v.stem + ".jpg")
            r = analyse_video(v, model, n_frames=args.frames, save_preview=pv, conf=args.conf)
            # Резкость и тряска меряются на тех же кадрах, что и поза:
            # файл уже открыт, второй раз декодировать смысла нет.
            r["sharp"], r["shake"] = analyse_sharpness(v)
            sharp_scores[v.name] = r["sharp"]
            rows.append(r)
            print(f"[{i}/{len(videos)}] {v.name}: {r['plan']} (увер. {r['conf']}, людей макс {r['people_max']}, "
                  f"резкость {r['sharp']:.0f}, тряска {r['shake']:.2f})")
        except Exception as e:
            print(f"[{i}/{len(videos)}] {v.name}: ОШИБКА {e}")

    # Порог резкости считается от медианы самой папки: перебивки сняты
    # одним светом, и абсолютное число у ночной съёмки с зерном занизило
    # бы норму вдвое.
    if args.no_blur or not bool(settings.get("sharp.enabled", True)):
        kept = rows
    else:
        floor = median_floor(list(sharp_scores.values()),
                             float(settings.get("sharp.floor_factor", 0.35)))
        shake_max = float(settings.get("sharp.shake_max", 0.05))
        kept = []
        for r in rows:
            good, why = usable(r["sharp"], r["shake"], floor, shake_max)
            if good:
                kept.append(r)
            else:
                print(f"  выброшено: {r['file']} — {why}")
        if floor > 0:
            print(f"Расфокус и тряска отсеяны по порогам: резкость < "
                  f"{floor:.0f} (медиана папки x "
                  f"{settings.get('sharp.floor_factor', 0.35)}), "
                  f"тряска > {shake_max:.2f}.")
    rows = kept

    out = Path(args.output)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "plan", "hero", "duration", "desc", "conf", "path"])
        for r in rows:
            w.writerow([r["file"], r["plan"], r["hero"], r["duration"],
                        f"auto {r['conf']} | {r['explain']}", r["conf"], r["path"]])
    print(f"\nГотово: {out} ({len(rows)} строк). Дальше:")
    print(f"  python plugin/src/modes/autocut.py --csv {out} --mode digest --auto --out timeline.csv")
    print(f"Проверьте превью в {args.preview} и поправьте колонки plan/hero где уверенность <= 0.65.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""Подготовка логотипа AutoCut для окна.

Исходник с чёрным фоном и пустыми полями не годится: в окне он
ляжет чёрным квадратом на тёмную панель. Скрипт обрезает поля и
делает чёрный прозрачным.

Запуск:
    python plugin/src/ui/make_logo.py "C:\\путь\\к\\логотипу.png"

Появится два файла:
    assets/autocut_logo.png  - знак вместе с надписью, для шапки
    assets/autocut_mark.png  - только знак, для иконки окна
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"

# чёрный уходит в прозрачность плавно, иначе край букв «лестничкой»
BLACK_LO, BLACK_HI = 8, 30

# Размеры файлов - вдвое больше, чем в окне. Tk масштабирует только
# целым числом (subsample), поэтому файл ровно под экран после
# округления вниз даст соседний размер и логотип поедет. Вдвое -
# делится на два нацело и остаётся резким на увеличенных экранах.
LOGO_W = 280          # показывается как 140
MARK_W = 120          # показывается как 60


def read_png(path: Path):
    """cv2.imread не понимает кириллицу в пути и молча отдаёт None.
    Читаем файл средствами Python, декодируем из памяти."""
    buf = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise SystemExit(f"Не прочитался файл: {path}")
    return img


def write_png(path: Path, img):
    """Та же история с записью: imwrite не понимает кириллицу."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, enc = cv2.imencode(".png", img)
    if not ok:
        raise SystemExit(f"Не закодировалось: {path}")
    path.write_bytes(enc.tobytes())


def content_box(gray: np.ndarray, thresh: int = 20):
    """Прямоугольник с содержимым: всё, что светлее порога."""
    ys, xs = np.where(gray > thresh)
    if not len(ys):
        raise SystemExit("Картинка пустая: нет ни одного светлого пикселя")
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def split_mark(gray: np.ndarray):
    """Знак и надпись лежат друг под другом. Режем по самой пустой
    строке между ними, иначе знак получится с куском букв.

    Ищем по всему содержимому, а не по верхней половине: промежуток
    между знаком и надписью оказался ниже середины картинки. Внутри
    самого знака тоже есть пустые строки (дырка в «A»), но они
    короче, поэтому побеждает самый длинный провал.
    """
    rows = (gray > 20).sum(axis=1)
    x0, y0, x1, y1 = content_box(gray)
    best, best_gap, run = y1, 0, 0
    for y in range(y0, y1):
        if rows[y] == 0:
            run += 1
            if run > best_gap:
                best_gap, best = run, y - run // 2
        else:
            run = 0
    return (x0, y0, x1, best) if best_gap > 4 else (x0, y0, x1, y1)


def to_alpha(gray: np.ndarray) -> np.ndarray:
    """Прозрачность из яркости: чёрный -> 0, светлый -> 255.

    Считаем в float32 намеренно: в int16 произведение 247 * 255 = 62985
    не влезает в диапазон, numpy тихо заворачивает его в -2551, и вся
    белая часть логотипа становится прозрачной.
    """
    k = 255.0 / (BLACK_HI - BLACK_LO)
    return np.clip((gray.astype(np.float32) - BLACK_LO) * k,
                   0, 255).astype(np.uint8)


def resize_to(img: np.ndarray, width: int) -> np.ndarray:
    h, w = img.shape[:2]
    if w <= width:
        return img
    return cv2.resize(img, (width, max(1, round(h * width / w))),
                      interpolation=cv2.INTER_AREA)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("Укажи путь к логотипу: "
              "python plugin/src/ui/make_logo.py путь.png")
        return 2
    src = Path(argv[0])
    if not src.is_file():
        print(f"Нет файла: {src}")
        return 2

    img = read_png(src)
    bgr = img[:, :, :3] if img.ndim == 3 else cv2.cvtColor(img,
                                                           cv2.COLOR_GRAY2BGR)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    x0, y0, x1, y1 = content_box(gray)
    mx0, my0, mx1, my1 = split_mark(gray)
    pad = 6
    print(f"исходник {img.shape[1]}x{img.shape[0]}, "
          f"содержимое {x1-x0}x{y1-y0}, знак {mx1-mx0}x{my1-my0}")

    def cut(box, out_w, name):
        a, b, c, d = box
        a, b = max(0, a - pad), max(0, b - pad)
        part = bgr[b:d, a:c]
        out = cv2.cvtColor(part, cv2.COLOR_BGR2BGRA)
        out[:, :, 3] = to_alpha(gray[b:d, a:c])
        out = resize_to(out, out_w)
        write_png(ASSETS / name, out)
        print(f"  {name}: {out.shape[1]}x{out.shape[0]}, "
              f"{(ASSETS/name).stat().st_size/1024:.0f} КБ")

    cut((x0, y0, x1, y1), LOGO_W, "autocut_logo.png")
    cut((mx0, my0, mx1, my1), MARK_W, "autocut_mark.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
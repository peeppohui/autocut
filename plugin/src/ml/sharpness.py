# -*- coding: utf-8 -*-
"""Резкость кадра и тряска камеры.

Стандарт медиацентра требует минимизировать расфокус и тряску при отборе
кадров. Обе вещи видно на кадрах до того, как ролик собран, и обе
молчаливые: перебивка вставится, и дымы мышью никто не заметит.

Как это меряется. Резкость — дисперсия оператора Лапласиана: у резкой
картинки много высоких частот и значение большое, у мыльной почти нет
границ. Важно, что сравниваются только КАДРЫ МЕЖДУ СОБОЙ: абсолютные
числа у ночной съёмки с зерном и у дневной с соляркой не совпадут, а
внутри одного ролика вполне.

Тряска — средняя разница соседних кадров. Важно: сама по себе разница
большая ещё и у быстрого движения, которое в эфире нормально. Поэтому
тряской считается не движение вообще, а то, что есть РЯДОМ с
неподвижными кадрами: встряхнувшийся кадр берёт резкость соседа и
движение, а спокойная панорама — нет. Поэтому одинокого быстрого кадра
не наказываем, а вот несколько подряд — да.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))


def laplacian_var(frame) -> float:
    """Дисперсия Лапласиана: чем больше, тем резче кадр."""
    import cv2
    import numpy as np
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def frame_diff(a, b) -> float:
    """Насколько кадр сдвинулся относительно соседа, в долях площади."""
    import cv2
    import numpy as np
    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.int16)
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.int16)
    d = np.abs(ga - gb)
    return float((d > 12).mean())


def sharpest(frames: Sequence, middle: bool = True):
    """Индекс самого резкого кадра.

    По умолчанию ищем в середине набора, а не во всём: стандарт требует
    брать кадр не с самого начала, смотреть середину. Начало и конец
    записи чаще всего дёргаются — там ещё не разогнался автофокус.
    """
    if not frames:
        return None
    lo, hi = 0, len(frames)
    if middle and len(frames) >= 5:
        lo, hi = 1, len(frames) - 1
    best, best_v = None, -1.0
    for i in range(lo, hi):
        v = laplacian_var(frames[i])
        if v > best_v:
            best, best_v = i, v
    return best


def frame_shift(a, b):
    """Насколько кадр b сдвинут относительно a, в пикселях.

    Фазовая корреляция, а не разница яркостей: она даёт именно сдвиг, и
    поэтому по ней можно отличить панораму от тряски. Разница яркостей
    этого не различает вовсе.
    """
    import cv2
    import numpy as np
    wa = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float32)
    wb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.float32)
    (dx, dy), resp = cv2.phaseCorrelate(wa, wb)
    return float(dx), float(dy), float(resp)


def jitter(run: Sequence) -> float:
    """Насколько сдвиг кадра скачет от кадра к кадру, в пикселях.

    Меряется РАЗБРОСОМ сдвига, а не величиной сдвига и не отношением
    пути к смещению. Обе альтернативы проверены на живой съёмке и
    обе врут:

    * величина сдвига не отличает тряску от панорамы — обе двигаются;
    * отношение пути к итоговому смещению ВЗРЫВАЕТСЯ на неподвижной
      камере: смещение там около нуля, и любой микроскопический шум
      превращается в отношение 13 и больше. На реальных файлах этим
      способом помечались как трясущиеся кадры со штатива (разброс
      0.11 px), а ручная камера с разбросом 2.0 px проходила.

    Разброс сдвига честно различает все три случая: у панорамы сдвиг
    каждый кадр примерно тот же, у камеры на штативе он около нуля и
    почти не гуляет, у трясущейся камеры скачет на единицы пикселей
    в обе стороны.

    Важно: кадры здесь ДОЛЖНЫ идти подряд. Разброс, посчитанный по
    кадрам, размазанным по всему клипу, измеряет не тряску, а движение
    объекта внутри кадра — а это как раз то, что стандарт запрещает
    меньше всего.
    """
    import numpy as np
    if len(run) < 4:
        return 0.0
    vs = [frame_shift(run[i], run[i + 1]) for i in range(len(run) - 1)]
    trusted = [(dx, dy) for dx, dy, r in vs if r > 0.25]
    use = trusted if len(trusted) >= len(vs) // 2 else [(dx, dy) for dx, dy, _ in vs]
    if len(use) < 3:
        return 0.0
    a = np.array([x for x, _ in use], dtype=np.float32)
    b = np.array([y for _, y in use], dtype=np.float32)
    return float(np.hypot(a.std(), b.std()))


def wobble(frames: Sequence) -> Tuple[float, float]:
    """Совместимость: (доля пути, средний шаг). Больше для отбора не
    используется — см. разбор в `jitter`."""
    import math
    if len(frames) < 3:
        return 1.0, 0.0
    vs = [frame_shift(frames[i], frames[i + 1]) for i in range(len(frames) - 1)]
    trusted = [(dx, dy) for dx, dy, r in vs if r > 0.25]
    use = trusted if len(trusted) >= 2 else [(dx, dy) for dx, dy, _ in vs]
    path = sum(math.hypot(dx, dy) for dx, dy in use)
    net = math.hypot(sum(dx for dx, _ in use), sum(dy for _, dy in use))
    return path / max(net, 1.0), path / max(len(use), 1)


def score(frames: Sequence, run: Optional[Sequence] = None
          ) -> Tuple[float, float]:
    """(резкость, тряска) для клипа.

    frames — кадры, размазанные по клипу: по ним честно меряется
    резкость, кадр берётся из середины набора.

    run — НЕПРЕРЫВНЫЙ кусок кадров из середины клипа, по нему меряется
    тряска. Если его не передали, тряска меряется по frames, и это
    заведомо хуже: см. `jitter`.
    """
    if not frames:
        return 0.0, 0.0
    sh = sorted(laplacian_var(f) for f in frames)
    sharp = sh[len(sh) // 2]
    return sharp, jitter(run if run is not None else frames)


def usable(sharp: float, shake: float, floor: float, shake_max: float,
           ) -> Tuple[bool, str]:
    """Годится ли клип. Возвращает (да, причина если нет).

    shake_max — разброс сдвига соседних кадров в пикселях, не «отношение».
    На живых файлах откалибровано: камера на штативе даёт 0.1-0.4, ручная
    камера в кадре 2 и больше.
    """
    if floor > 0 and sharp < floor:
        return False, ("в расфокусе: резкость {:.0f} при пороге {:.0f}"
                       .format(sharp, floor))
    if shake > shake_max:
        return False, ("тряска: сдвиг скачет на {:.1f} px, порог {:.1f}"
                       .format(shake, shake_max))
    return True, ""


def median_floor(values: Sequence[float], factor: float = 0.35) -> float:
    """Порог резкости для папки: доля от её собственной медианы.

    Медиана, а не минимум: папка перебивок почти всегда снята одним
    светом, и если ориентироваться на самый мыльный кадр, в порог
    попадёт половина нормального материала.
    """
    vals = sorted(v for v in values if v > 0)
    if not vals:
        return 0.0
    return vals[len(vals) // 2] * factor
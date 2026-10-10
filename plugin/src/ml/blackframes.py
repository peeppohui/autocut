# -*- coding: utf-8 -*-
"""Поиск участков, где в кадре ничего нет: камера не включена, крышка
на объективе, съёмка в темноте. Такой кусок в монтаж попадать не должен —
звук-то есть, а смотреть нечего.

Считается через фильтр blackdetect в ffmpeg: он один проходит по
кадрам и печатает интервалы в stderr. Разбирать надо осторожно: когда
чёрное тянется до конца файла, ffmpeg не пишет black_end, только
black_start и black_duration.

Результат кладём в кэш рядом с результатом: файлы на гигабайты,
пересчитывать при каждой правке транскрипта незачем.
"""
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import List, Sequence, Tuple

# интервал: с чёрным концом (обычный случай) и без него (чёрное до EOF)
_PAT_END = re.compile(r"black_start:([\d.]+)\s+black_end:([\d.]+)")
_PAT_DUR = re.compile(r"black_start:([\d.]+)\s+black_duration:([\d.]+)")


def parse_blackdetect(stderr: str) -> List[Tuple[float, float]]:
    """Интервалы чёрного из вывода ffmpeg blackdetect."""
    out = []
    for line in stderr.splitlines():
        m = _PAT_END.search(line)
        if m:
            out.append((float(m.group(1)), float(m.group(2))))
            continue
        m = _PAT_DUR.search(line)
        if m:
            t0 = float(m.group(1))
            out.append((t0, t0 + float(m.group(2))))
    return out


def _cache_path(video: Path, cache_dir: Path, min_dur: float,
                pix_th: float, sample_fps: float) -> Path:
    st = video.stat()
    key = (f"{video.resolve()}|{st.st_size}|{int(st.st_mtime)}|"
           f"{min_dur}|{pix_th}|{sample_fps}")
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"black_{h}.json"


def black_intervals(video, cache_dir=None, min_dur: float = 0.5,
                    pix_th: float = 0.10,
                    sample_fps: float = 2.0) -> List[Tuple[float, float]]:
    """Интервалы без картинки в секундах. Пустой список = кадр везде."""
    video = Path(video)
    if not video.is_file():
        return []
    cache = None
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache = _cache_path(video, cache_dir, min_dur, pix_th, sample_fps)
        if cache.is_file():
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                return [(float(a), float(b)) for a, b in data]
            except (ValueError, OSError):
                pass  # кэш побит - пересчитаем
    # Кадр уменьшаем и берём не все кадры, а sample_fps в секунду: чёрное
    # длится десятками секунд (камера не включена), а ловить вспышки в
    # одну-две кадра смысла нет. Декодирование - узкое место, и отбор
    # кадров ускоряет проход с полутора минут до минуты на 12 минут видео.
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video),
         "-vf", (f"fps={sample_fps},scale=320:-1,"
                 f"blackdetect=d={min_dur}:pix_th={pix_th}"),
         "-an", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    iv = parse_blackdetect(proc.stderr or "")
    if cache is not None:
        try:
            cache.write_text(json.dumps(iv), encoding="utf-8")
        except OSError:
            pass
    return iv


def covered(intervals: Sequence[Tuple[float, float]],
            t0: float, t1: float) -> float:
    """Сколько секунд из [t0, t1] приходится на чёрное."""
    if t1 <= t0:
        return 0.0
    total = 0.0
    for a, b in intervals:
        lo = max(a, t0)
        hi = min(b, t1)
        if hi > lo:
            total += hi - lo
    return total


def is_black(intervals: Sequence[Tuple[float, float]], t0: float, t1: float,
             min_ratio: float = 0.8) -> bool:
    """Кусок без картинки, если чёрным закрыто не меньше min_ratio."""
    if not intervals:
        return False
    return covered(intervals, t0, t1) >= (t1 - t0) * min_ratio

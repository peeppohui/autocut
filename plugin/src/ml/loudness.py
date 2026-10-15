# -*- coding: utf-8 -*-
"""Громкость дорожек: довести голоса до одного уровня.

Задача. На интервью микрофон гостя тише микрофона ведущего, а оба
тише, чем принято для видео. Замерено на настоящем материале: ведущий
-42.0 LUFS, гость -47.5 LUFS, разница 5.6 дБ. Оба дорожки ещё и очень
тихие: для соцсетей нужно около -16 LUFS.

Почему R128, а не «средняя громкость в децибелах». Среднее арифметическое
по отсчётам зависит от пауз и от того, много ли в записи шума: одна и та же
речь с разным уровнем шума даст разное среднее. R128 смотрит на
интегрированную громкость с учётом громкости и коротких пиков, и это тот
же ориентир, которым пользуются вещатели.

Почему усиление с ограничителем, а не `loudnorm`. Оба варианта проверены
на живой речи с большим размахом динамики:

    исходно              -47.5 LUFS   TP -21.9   LRA 20.1
    loudnorm линейный    -18.0 LUFS   TP  -1.4   LRA 13.9   9 сек
    loudnorm динамич.    -18.0 LUFS   TP  -1.4   LRA 13.9   9 сек
    усиление + alimiter  -17.9 LUFS   TP  -0.4   LRA 18.8   меньше секунды

loudnorm попадает в пики и сам сжимает размах динамики с 20.1 до 13.9:
разговор начинает звучать ровнее и глуше. Обычное усиление с
ограничителем пиков размах сохраняет, а голоса слышно как есть. Для
интервью это честнее. И на два часа материала оно в десять раз быстрее.

Про истинный пик. `alimiter` ограничивает ПИК ОТСЧЁТОВ, а не истинный:
на живой дорожке предел -3.0 dB давал измеренный истинный пик -0.76,
то есть промах вверх на два с лишним децибела. Поэтому после обработки
результат перемеряется, и если истинный пик всё ещё выше цели,
опускается сам предел ограничителя. Убавлять усиление бесполезно:
громкие места всё равно упираются в потолок, и сколько усиление ни
снимай, пик остаётся на потолке - проверено тремя попытками подряд.

Чего модуль не делает. Он не сжимает динамику, не убирает шум и не
разводит голоса по высоте. Это нормализация уровня, а не реставрация.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bootstrap  # noqa: F401  — регистрирует src/config в sys.path
import settings

_JSON_RX = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


def enabled() -> bool:
    return bool(settings.get("audio.normalize", True))


def target_lufs() -> float:
    return float(settings.get("audio.target_lufs", -16.0))


def true_peak_db() -> float:
    return float(settings.get("audio.true_peak_db", -1.5))


def limiter_margin_db() -> float:
    """Запас ограничителя. alimiter держит пик отсчётов, а не истинный,
    и промахивается вверх на децибел-полтора."""
    return float(settings.get("audio.limiter_margin_db", 1.5))


# ------------------------------------------------------------- чистая логика

def gain_db(measured_lufs: float, target: float) -> float:
    """Сколько децибел добавить, чтобы из измеренной громкости вышла целевая."""
    return float(target) - float(measured_lufs)


def limiter_limit(target_tp: float, margin: float) -> float:
    """Предел ограничителя с запасом на истинный пик."""
    return float(target_tp) - float(margin)


def chain_filter(gain: float, limit: float,
                 attack: float = 5.0, release: float = 50.0) -> str:
    """Цепочка: ровное усиление, затем ограничение пиков.

    latency=1 включает опережение ограничителя - без него пик успевает
    пройти и щёлкает в монтаже.
    """
    return (f"volume={gain:.2f}dB,"
            f"alimiter=limit={limit:.2f}dB:level=disabled:"
            f"attack={attack:.1f}:release={release:.1f}:latency=1")


# ------------------------------------------------------------- работа с ffmpeg

def measure(path: Path) -> Optional[Dict[str, float]]:
    """Громкость файла по R128. None, если не измерилось.

    Один проход ffmpeg ничего не пишет на диск: он только декодирует и
    анализирует, поэтому замер безопасен на исходниках.
    """
    p = Path(path)
    if not p.is_file():
        return None
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(p),
         "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    found = _JSON_RX.findall(proc.stderr or "")
    if not found:
        return None
    try:
        raw = json.loads(found[-1])
    except ValueError:
        return None
    try:
        return {"lufs": float(raw["input_i"]),
                "true_peak": float(raw["input_tp"]),
                "lra": float(raw["input_lra"]),
                "threshold": float(raw["input_thresh"])}
    except (KeyError, TypeError, ValueError):
        return None


def _probe(path: Path) -> Dict[str, int]:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=channels,sample_rate", "-of", "json",
         str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        st = json.loads(proc.stdout)["streams"][0]
        return {"channels": int(st.get("channels") or 1),
                "rate": int(st.get("sample_rate") or 48000)}
    except Exception:
        return {"channels": 1, "rate": 48000}


def normalize(src: Path, dst: Optional[Path] = None,
              target: Optional[float] = None, tp: Optional[float] = None,
              margin: Optional[float] = None, attempts: int = 2) -> Dict:
    """Приводит файл к целевой громкости. Возвращает отчёт.

    Отчёт всегда заполнен, даже если ничего не вышло: вызывающий код
    решает, что делать, а нормализация - улучшение, а не условие сборки.

    dst = None означает перезаписать исходный файл. Перезаписывается
    только то, что программа сама создала как промежуточный файл;
    пользовательские записи не трогаются.
    """
    src = Path(src)
    dst = Path(dst) if dst else src
    rep: Dict = {"file": str(src), "ok": False, "why": "",
                 "before": None, "after": None, "gain_db": 0.0}
    before = measure(src)
    if before is None:
        rep["why"] = "не измерилась громкость - оставляю как есть"
        return rep
    rep["before"] = before

    if target is None:
        target = target_lufs()
    if tp is None:
        tp = true_peak_db()
    if margin is None:
        margin = limiter_margin_db()
    probe = _probe(src)
    limit = limiter_limit(tp, margin)
    gain = gain_db(before["lufs"], target)

    # Перезаписывать входной файл самим ffmpeg нельзя: «Error opening
    # output files: Invalid argument». Поэтому рядом пишем временный
    # файл и подменяем исходник только после успеха. Если обработка
    # сорвалась, дорожка остаётся нетронутой - нормализация не имеет
    # права убить звук.
    same = dst.resolve() == src.resolve()
    out = dst if not same else src.with_name(src.stem + ".norm.wav")
    out.parent.mkdir(parents=True, exist_ok=True)

    for _ in range(max(1, attempts)):
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(src),
             "-af", chain_filter(gain, limit),
             "-ar", str(probe["rate"]), "-ac", str(probe["channels"]),
             "-c:a", "pcm_s24le", str(out)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace")
        if proc.returncode != 0:
            rep["why"] = "ffmpeg: " + ((proc.stderr or "").strip()
                                        .splitlines() or ["?"])[-1]
            _drop(out)
            return rep
        after = measure(out)
        rep["gain_db"] = gain
        rep["after"] = after
        if after is None:
            rep["why"] = "после обработки громкость не измерилась"
            _drop(out)
            return rep
        rep["ok"] = True
        if after["true_peak"] <= tp + 0.2:
            _commit(out, src, dst, same)
            return rep
        # Истинный пик всё ещё выше цели, и увеличение запаса тут не
        # помогает: пики упираются в ПОТОЛОК ограничителя, сколько
        # усиление ни убавляй, они останутся на потолке. Проверено на
        # живой дорожке: три попытки срезать усиление дали один и тот
        # же пик -0.76 dBTP. Двигать надо сам предел.
        limit -= (after["true_peak"] - tp)
        rep["why"] = (f"истинный пик вылез на {after['true_peak']:.1f} "
                      f"dBTP, опускаю предел ограничителя до "
                      f"{limit:.1f} dB")
    _commit(out, src, dst, same)
    if rep.get("why"):
        rep["why"] += " (громкость при этом немного падает)"
    return rep


def _drop(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _commit(out: Path, src: Path, dst: Path, same: bool) -> None:
    """Переносит обработанный файл на место назначения."""
    if same:
        os.replace(str(out), str(src))
        return
    if dst.exists():
        dst.unlink()
    os.replace(str(out), str(dst))


def describe(rep: Dict) -> str:
    """Строка для лога сборки."""
    if not rep.get("ok"):
        return f"{Path(rep['file']).name}: {rep.get('why') or 'не вышло'}"
    b, a = rep.get("before") or {}, rep.get("after") or {}
    txt = (f"{Path(rep['file']).name}: "
           f"{b.get('lufs', 0):.1f} -> {a.get('lufs', 0):.1f} LUFS "
           f"(+{rep.get('gain_db', 0):.1f} дБ), пик "
           f"{b.get('true_peak', 0):.1f} -> {a.get('true_peak', 0):.1f} dBTP")
    if rep.get("why"):
        txt += f"; {rep['why']}"
    return txt
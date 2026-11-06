# -*- coding: utf-8 -*-
"""Звук с рекордера для голосовых кусков.

В материале микрофон стоял на рекордере, а не на камере. Поэтому в
эталонном проекте голос лежит на отдельной звуковой дорожке: картинка
с камеры, звук с REC00016. Так и делаем — колонка `audio` в плане
уже умеет подкладывать отдельный звуковой файл к клипу.

Подбор рекордера — по словам (`ml/recmatch.py`): огибающая звука на
шуме зала совпадает плохо (замер на стендапе: 28.5 с против
ожидаемых 31.6 с при качестве 0.15), а слова те же самые.

Проверено на эталонных парах:
  C0098 185.00-193.16 <-> REC00006 451.76  (ошибка 0.5 с)
  C0100 151.20-181.72 <-> REC00008 214.64  (ошибка 0.2 с)
  стендап C0109         <-> REC00016       (сдвиг 6.2 с)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SRC / "config") not in sys.path:
    sys.path.insert(0, str(SRC / "config"))

import bootstrap            # noqa: E402,F401
import settings             # noqa: E402

# Расширения, которые умеет читать ffmpeg и которые встречаются у
# рекордеров. Регистр не важен: на карте памяти бывает и .WAV, и .wav.
REC_EXT = {".wav", ".mp3", ".aif", ".aiff", ".m4a", ".flac"}

_CODEC = {16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_s32le"}


def is_rec_file(path: Path) -> bool:
    """Файл похож на запись рекордера."""
    try:
        p = Path(path)
        return (p.is_file() and p.suffix.lower() in REC_EXT
                and p.stem.startswith("REC"))
    except OSError:
        return False


def probe_duration(path: Path) -> float:
    """Длительность исходника в секундах, либо 0.0, если ffprobe не смог.

    В XML у файла рекордера должна стоять настоящая длительность, а не
    длина камерного куска: Premiere по ней проверяет, что `out` не
    выходит за пределы файла. Ошибка здесь не роняет импорт сразу, но
    клип может обрезаться или не проиграться.
    """
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=20)
        return max(0.0, float((r.stdout or "").strip()))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


def find_rec_dir(root: Path):
    """Папка с файлами рекордера рядом с частями материала."""
    root = Path(root)
    cands = [root.parent / "звк", root / "звк", root.parent / "звук",
             root.parent, root]
    for cand in cands:
        if not cand.is_dir():
            continue
        try:
            for p in cand.iterdir():
                if is_rec_file(p):
                    return cand
        except OSError:
            continue
    return None


def rec_index(rec_dir: Path, work: Path, model: str = "large-v3", log=print):
    """Слова всех файлов рекордера: {имя: [(слово, нач, кон)]}.

    Кеш лежит в `work`, а не рядом с исходниками: папка материала может
    быть на карте памяти и только для чтения, да и мусорить в исходниках
    не место. `speech.words_of` сам проверяет модель и исходник, поэтому
    старый кеш от другого файла с тем же именем не подсунётся.
    """
    import speech

    cache = Path(work) / "rec"
    cache.mkdir(parents=True, exist_ok=True)
    index = {}
    for src in sorted(Path(rec_dir).iterdir()):
        if not is_rec_file(src):
            continue
        try:
            words = speech.words_of(src, cache, model, "auto")
        except Exception as e:
            log("  рекордер {}: не распознан: {}".format(
                src.name, str(e)[:120]))
            continue
        if not words:
            log("  рекордер {}: речи нет".format(src.name))
            continue
        index[src.stem] = [(w, s, e) for w, s, e in words]
    return index


def cut(rec_path: Path, start: float, dur: float, out_wav: Path,
        rate: int | None = None, channels: int | None = None,
        depth: int | None = None, log=None) -> bool:
    """Вырезать кусок звука рекордера в файл для дорожки.

    Формат берётся из настроек экспорта: разрядность в XML и в файле
    должна совпадать, иначе ползунок громкости в Premiere перестаёт
    значить что-либо.
    """
    if dur <= 0.05:
        return False
    # Отрицательное начало означает, что кусок начинается раньше
    # рекордера. Обрезать с нуля нельзя: звук уедет относительно
    # картинки, и лучше оставить камеру, чем подложить сдвинутый голос.
    if start < -0.25:
        if log:
            log("  рекордер {}: начало {:.2f}с раньше записи".format(
                Path(rec_path).name, start))
        return False

    exp = settings.section("export")
    rate = int(exp.get("audio_sample_rate", 48000) if rate is None else rate)
    channels = int(exp.get("audio_channels", 2) if channels is None else channels)
    depth = int(exp.get("audio_depth", 16) if depth is None else depth)
    codec = _CODEC.get(depth)
    if not codec or rate <= 0 or channels <= 0:
        if log:
            log("  рекордер: неподдерживаемый формат {} Гц, {} каналов, {} бит"
                .format(rate, channels, depth))
        return False

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error",
           "-ss", "{:.3f}".format(max(0.0, start)),
           "-t", "{:.3f}".format(dur), "-i", str(rec_path),
           "-vn", "-ar", str(rate), "-ac", str(channels),
           "-c:a", codec, str(out_wav)]
    r = subprocess.run(cmd, capture_output=True)
    ok = r.returncode == 0 and out_wav.is_file() and out_wav.stat().st_size > 0
    if not ok and log:
        err = (r.stderr or b"").decode("utf-8", "replace").strip()
        tail = err.splitlines()[-1] if err else "код {}".format(r.returncode)
        log("  рекордер {}: ffmpeg не смог: {}".format(
            Path(rec_path).name, tail[:160]))
    return ok

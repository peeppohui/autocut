#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoCut Interview — рыба интервью по тексту (без исходников монтажа не надо,
нужен сам файл интервью).

Что делает:
  1. Транскрибирует речь (faster-whisper, русский, пословные тайминги).
  2. Жмёт паузы: тишина длиннее max_pause урезается до max_pause.
  3. Режет мусор: короткие паразиты («э», «м», «ну» < 0.4с) и повторы
     («и и», «вот вот») — сразу; длинные мусорные фразы — только если есть
     чем прикрыть (перебивка из --broll), иначе уходят в отчёт «вручную».
  4. Собирает смысловые куски (предложения) в selects.csv — готовый таймлайн
     с ТОЧНЫМИ in/out для premiere.py. Каждый внутренний стык прикрывается
     перебивкой с --broll (вставляется встык, 1.2-2с).

Использование:
  # одно видео (как было):
  python interview.py --input интервью.mp4 --broll clips_auto.csv --outdir rz
  # мультикамера: сначала sync.py, потом (файлы уже на общей шкале):
  python interview.py --wide synced/ref.mp4 --guest-cam synced/cam.mp4 \
      --guest-mic synced/mic_guest.wav --host-mic synced/mic_host.wav \
      --broll clips_auto.csv --outdir rz
  python premiere.py --timeline rz/selects.csv --media-dir . --out ryba.xml --trim-rec
  # --trim-rec точные куски не трогает, режет только перебивки по норме плана

Нужно один раз: python -m pip install faster-whisper
Модель base качается сама (~150 МБ). Для точности: --model small (~500 МБ).

Честные ограничения:
  - Смысл не понимает: «лучшие дубли» не выбирает, порядок не меняет (хронология).
  - Без --broll стыки внутри одного говорящего останутся jump cut'ами —
    отчёт перечислит их таймкодами под ручную правку / J-cut.
"""

from __future__ import annotations
import argparse
import csv
import re
import subprocess
import sys
import wave
from collections import Counter
from pathlib import Path
from typing import List, Dict, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bootstrap  # noqa: F401  — регистрирует src/config в sys.path
import settings
import blackframes

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

if sys.platform == "win32":
    # CUDA DLL из pip-пакетов nvidia-* — через общий gpu-модуль.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import gpu as _gpu

        _gpu.bootstrap()
    except Exception:
        pass

# --- мусорные слова (строчные, без пунктуации) ---
SHORT_FILLERS = {"э", "а", "м", "хм", "эм", "ну", "вот", "да", "так", "это"}
# одиночные слова короче min_keep: выживают только эти (короткие ответы),
# остальное («по», «васа», «нет» в пустоте) — обрезки, а не реплики
KEEP_ONE = {"да", "нет", "ага", "угу", "конечно", "точно", "привет", "пока",
            "спасибо", "здравствуйте", "алло"}
PHRASE_FILLERS = [
    "как бы", "типа", "значит", "так сказать", "короче говоря", "короче",
    "собственно говоря", "собственно", "это самое", "ну вот", "вот так",
    "так вот", "в общем", "в принципе", "честно говоря",
]
SENT_START_DROP = {"ну", "вот", "так", "значит", "итак"}  # в начале предложения — выкидываем
MAX_SHORT_FILLER = float(settings.get("interview.max_short_filler_sec", 0.4))  # короткие паразиты длиннее — только с прикрытием/вручную

# Мат и технический трёп: режем сразу (рыба, не финал — смысл не судим).
# Точное совпадение целых слов (не префиксы: «художник» под «ху» не попадёт).
# Свой список: файл badwords.txt рядом со скриптом, по слову на строку.
MUTE_WORDS = {
    "бля", "блять", "блядь", "бляха", "нах", "нахуй", "нахер", "нафиг",
    "хуй", "хуйня", "хуево", "хуёво", "пизда", "пиздец", "пиздеть",
    "ебать", "ебаный", "ебанутый", "сука", "суки", "заебал", "заебало",
    "охуеть", "нихуя", "дохуя", "мудак", "твоюмать",
    "проверка", "тест", "слышно", "пишется", "запись",
}
MUTE_PHRASES = {"проверка звука", "раз два три", "тест микрофона"}


def load_mute_list() -> set:
    words = set(MUTE_WORDS)
    p = Path(__file__).resolve().parent / "badwords.txt"
    if p.is_file():
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip().lower()
            if line and not line.startswith("#"):
                words.add(line)
    return words


class Word:
    __slots__ = ("w", "start", "end", "drop", "why", "prob")
    def __init__(self, w, start, end, prob=1.0):
        self.w = w
        self.start = float(start)
        self.end = float(end)
        self.drop = False
        self.why = ""
        self.prob = float(prob if prob is not None else 1.0)


def norm(w: str) -> str:
    return re.sub(r"[^\w\-]", "", w.lower())


def extract_wav(video: Path, out: Path):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(video),
         "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(out)],
        check=True)


def load_mono16(path: Path):
    import numpy as np
    with wave.open(str(path), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def make_model(model_name: str, device: str = "auto"):
    """Whisper с выбором устройства: cuda(float16) или cpu(int8).
    auto = cuda если завелась, иначе тихо cpu."""
    from faster_whisper import WhisperModel
    import gpu as _gpu

    dev = _gpu.pick(device)
    if dev == "cuda":
        try:
            return WhisperModel(model_name, device="cuda", compute_type="float16"), dev
        except Exception as e:
            print(f"GPU не завелась ({e}), падаю на CPU.")
            return WhisperModel(model_name, device="cpu", compute_type="int8"), "cpu"
    return WhisperModel(model_name, device="cpu", compute_type="int8"), dev


def transcribe(wav: Path, model_name: str, device: str = "auto") -> Tuple[List[Word], List[Dict]]:
    audio = load_mono16(wav)
    model, dev = make_model(model_name, device)
    print(f"Транскрибация на {dev.upper()}...")
    segments, info = model.transcribe(audio, language="ru", word_timestamps=True,
                                      vad_filter=True)
    segments = list(segments)  # генератор одноразовый — материализуем сразу
    words: List[Word] = []
    sents: List[Dict] = []
    for s in segments:
        if not s.words:
            continue
        for wd in s.words:
            words.append(Word(wd.word.strip(), wd.start, wd.end,
                              getattr(wd, "probability", 1.0)))
    sents = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments if s.words]
    print(f"Язык: {info.language}, сегментов: {len(sents)}, слов: {len(words)}")
    return words, sents


def transcribe_audio_array(audio, model, label: str) -> Tuple[List[Word], List[Dict]]:
    """Транскрибация уже загруженного массива (мультикамера: модель одна на всех)."""
    segments, info = model.transcribe(audio, language="ru", word_timestamps=True,
                                      vad_filter=True)
    segments = list(segments)
    words: List[Word] = []
    for s in segments:
        if not s.words:
            continue
        for wd in s.words:
            words.append(Word(wd.word.strip(), wd.start, wd.end,
                              getattr(wd, "probability", 1.0)))
    sents = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments if s.words]
    print(f"{label}: сегментов {len(sents)}, слов {len(words)}")
    return words, sents


def rms_in(x, sr: int, t0: float, t1: float) -> float:
    import numpy as np
    a = max(0, int(t0 * sr))
    b = min(len(x), int(t1 * sr))
    if b <= a:
        return 0.0
    seg = x[a:b]
    return float(np.sqrt((seg ** 2).mean()))


def resolve_overlaps(kept_g: List[Word], kept_h: List[Word],
                     audio_g, audio_h, sr: int = 16000) -> int:
    """УСТАРЕЛО пословно — оставлено для совместимости, мультикамера теперь
    идёт через group_utterances (победитель на всю реплику, а не на слово:
    иначе одна фраза остаётся в обоих микрофонах — как «Прикосновение
    волшебное» одновременно у гостя и ведущего)."""
    return _resolve_wordwise(kept_g, kept_h, audio_g, audio_h, sr)


def _resolve_wordwise(kept_g, kept_h, audio_g, audio_h, sr=16000) -> int:
    """Одновременная речь в оба микрофона: оставляем того, кто громче
    (проникновение в чужой микрофон тише). Возвращает число снятых слов."""
    dropped = 0
    for wg in kept_g:
        if wg.drop:
            continue
        for wh in kept_h:
            if wh.drop:
                continue
            if wg.start < wh.end and wh.start < wg.end:
                t0, t1 = max(wg.start, wh.start), min(wg.end, wh.end)
                if rms_in(audio_g, sr, t0, t1) >= rms_in(audio_h, sr, t0, t1):
                    wh.drop, wh.why = True, "перекрыт гостем"
                else:
                    wg.drop, wg.why = True, "перекрыт ведущим"
                dropped += 1
    return dropped


def group_utterances(words_g: List[Word], words_h: List[Word],
                     audio_g, audio_h, sr: int = 16000,
                     link_gap: float = float(settings.get("interview.mic_link_gap_sec", 0.3)),
                      ambig_db: float = float(settings.get("interview.ambiguous_db", 3.0))):
    """Склеивает пересекающиеся/близкие слова обоих микрофонов в реплики
    (зазор <= link_gap) и отдаёт КАЖДУЮ реплику целиком одному говорящему —
    тому, кто громче на всём её протяжении.

    Возвращает (снято_слов, спорные_интервалы). Спорно = разница микрофонов
    меньше ambig_db: петлички рядом, уровни почти равны — кому отдать,
    неясно. Такие куски метятся [~], а не молча приписываются.

    Пословный дележ давал дубли («Прикосновение волшебное» и у гостя,
    и у ведущего): границы слов у двух транскриптов разные, и каждое
    слово «выигрывал» свой микрофон."""
    events = sorted([(w.start, w.end, "г", w) for w in words_g if not w.drop] +
                    [(w.start, w.end, "в", w) for w in words_h if not w.drop])
    groups = []  # [t0, t1, [w_g], [w_h]]
    for s, e, sp, w in events:
        if groups and s - groups[-1][1] <= link_gap:
            g = groups[-1]
            g[1] = max(g[1], e)
            g[2 if sp == "г" else 3].append(w)
        else:
            groups.append([s, e, [w] if sp == "г" else [], [w] if sp == "в" else []])
    dropped = 0
    ambiguous = []
    for t0, t1, wg, wh in groups:
        if not wg or not wh:
            continue
        import math
        eg = rms_in(audio_g, sr, t0, t1)
        eh = rms_in(audio_h, sr, t0, t1)
        db = 20 * math.log10(max(eg, 1e-6) / max(eh, 1e-6))
        if abs(db) < ambig_db:
            ambiguous.append((t0, t1))
        if eg >= eh:
            for w in wh:
                w.drop, w.why = True, "дубль в микрофоне ведущего"
                dropped += 1
        else:
            for w in wg:
                w.drop, w.why = True, "дубль в микрофоне гостя"
                dropped += 1
    return dropped, ambiguous


def build_multicam_chunks(words_g: List[Word], words_h: List[Word],
                          merge_gap: float, max_pause: float,
                          min_keep: float):
    """Куски вида (in, out, speaker, text). Граница — всегда смена говорящего
    или пауза > merge_gap внутри одного."""
    events = sorted([(w.start, w.end, "гость", w) for w in words_g if not w.drop] +
                    [(w.start, w.end, "ведущий", w) for w in words_h if not w.drop])
    chunks = []
    cut_pauses = 0.0
    cur = None  # [in, end, speaker, words]
    for s, e, sp, w in events:
        if cur is None:
            cur = [s, e, sp, [w]]
            continue
        gap = s - cur[1]
        if sp != cur[2] or gap > merge_gap:
            if gap > max_pause and sp == cur[2]:
                cut_pauses += gap - max_pause
            if cur[1] - cur[0] >= min_keep and not _close_lone(cur[3]):
                chunks.append((cur[0], cur[1], cur[2], " ".join(x.w for x in cur[3])))
            cur = [s, e, sp, [w]]
        else:
            cur[3].append(w)
            cur[1] = e
    if cur is not None and cur[1] - cur[0] >= min_keep and not _close_lone(cur[3]):
        chunks.append((cur[0], cur[1], cur[2], " ".join(x.w for x in cur[3])))
    return chunks, cut_pauses


def drop_lowprob(words: List[Word], min_prob: float) -> int:
    """Слова, в которых модель не уверена (проникновение в чужой микрофон,
    гул, галлюцинации на шуме). Возвращает число снятых."""
    n = 0
    for w in words:
        if not w.drop and w.prob < min_prob:
            w.drop, w.why = True, f"неуверенно ({w.prob:.2f})"
            n += 1
    return n


def mark_trash(words: List[Word]):
    """Размечает мусор. Возвращает (auto_drop, need_cover, manual)."""
    auto_drop, need_cover, manual = [], [], []
    mute = load_mute_list()
    n = len(words)
    for i, wd in enumerate(words):
        t = norm(wd.w)
        dur = wd.end - wd.start
        # мат и техтрёп — сразу, без обсуждений
        if t in mute:
            wd.drop, wd.why = True, "мат/тех"
            auto_drop.append(wd)
            continue
        # повторы рядом: «и и», «вот вот»
        if i and t and t == norm(words[i - 1].w) and t not in {"я", "он", "она", "мы"}:
            wd.drop, wd.why = True, "повтор"
            auto_drop.append(wd)
            continue
        # начало предложения после паузы/пунктуации
        prev_end = words[i - 1].w if i else "."
        sent_start = (i == 0) or re.search(r"[.!?…:]$", prev_end.strip()) or \
                     (wd.start - words[i - 1].end > 1.0)
        if sent_start and t in SENT_START_DROP:
            wd.drop, wd.why = True, "зачин"
            auto_drop.append(wd)
            continue
        # короткие паразиты
        if t in SHORT_FILLERS and dur <= MAX_SHORT_FILLER:
            wd.drop, wd.why = True, "паразит"
            auto_drop.append(wd)
            continue
        if t in SHORT_FILLERS:
            manual.append(wd)  # длинный — резать только с прикрытием/вручную
    # фразовые паразиты (биграммы/триграммы)
    joined = " ".join(norm(w.w) for w in words)
    for ph in PHRASE_FILLERS:
        for m in re.finditer(r"\b" + re.escape(ph) + r"\b", joined):
            pass  # ниже — по словам, чтобы не резать внутри смысла вслепую
    # фразы ищем по окнам слов (только целиком внутри одного предложения-окна)
    L = max([len(p.split()) for p in PHRASE_FILLERS] + [len(p.split()) for p in MUTE_PHRASES])
    for i in range(n):
        for ln in range(2, L + 1):
            if i + ln > n:
                continue
            cand = " ".join(norm(words[i + k].w) for k in range(ln))
            if cand in MUTE_PHRASES:
                for s in words[i:i + ln]:
                    if not s.drop:
                        s.drop, s.why = True, "мат/тех"
                        auto_drop.append(s)
            elif cand in PHRASE_FILLERS:
                span = words[i:i + ln]
                if all(not s.drop for s in span):
                    need_cover.extend(span)
    return auto_drop, need_cover, manual


def _lone_trash(words) -> bool:
    """Одиночное слово-обрезок («по», «васа», «нет» в пустоте): выкидываем,
    кроме коротких ответов из KEEP_ONE."""
    return len(words) == 1 and norm(words[0].w) not in KEEP_ONE


def _close_lone(cur_words) -> bool:
    if _lone_trash(cur_words):
        cur_words[0].drop, cur_words[0].why = True, "огрызок"
        return True
    return False


def build_keeps(words: List[Word], max_pause: float, merge_gap: float,
                min_keep: float) -> Tuple[List[Tuple[float, float, str]], float]:
    """Склейка kept-слов в куски. Возвращает ([(in,out,text)], вырезано_пауз_сек)."""
    kept = [w for w in words if not w.drop]
    chunks: List[Tuple[float, float, str]] = []
    cut_pauses = 0.0
    cur_in, cur_words = None, []
    prev_end = None
    for w in kept:
        if cur_in is None:
            cur_in, cur_words, prev_end = w.start, [w], w.end
            continue
        gap = w.start - prev_end
        if gap > max_pause:
            cut_pauses += gap - max_pause
        if gap > merge_gap:
            # закрываем кусок
            if prev_end - cur_in >= min_keep and not _close_lone(cur_words):
                chunks.append((cur_in, prev_end, " ".join(x.w for x in cur_words)))
            elif chunks:
                pass  # крошку < min_keep выбрасываем (слово-паразит уровня фразы)
            cur_in, cur_words = w.start, [w]
        else:
            cur_words.append(w)
        prev_end = w.end
    if cur_in is not None and prev_end - cur_in >= min_keep and not _close_lone(cur_words):
        chunks.append((cur_in, prev_end, " ".join(x.w for x in cur_words)))
    return chunks, cut_pauses


def write_numbered_transcript(path: Path, chunks) -> None:
    """Транскрипт, который можно править руками: строки с '-' в начале
    будут выкинуты при сборке (--from-transcript)."""
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Режь прямо здесь: строки с '-' в начале НЕ войдут в рыбу.\n")
        f.write("# Формат не ломай: [N] [t0-t1] ГОСТЬ: текст\n")
        for n, (i, o, sp, text) in enumerate(chunks, 1):
            tag = "ГОСТЬ" if sp == "гость" else "ВЕДУЩИЙ"
            f.write(f"[{n}] [{i:.2f}-{o:.2f}] {tag}: {text}\n")


RX_LINE = re.compile(
    r"^\s*(?P<drop>[-xX#]\s*)?\[(\d+)\]\s*\[([\d.]+)-([\d.]+)\]\s*"
    r"(ГОСТЬ|ВЕДУЩИЙ)\s*:\s*(?P<text>.*)$")


def load_edited_transcript(path: Path):
    """Читает правленый транскрипт. Возвращает [(in,out,speaker,text)]."""
    chunks = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        m = RX_LINE.match(line)
        if not m or m.group("drop"):
            continue
        i, o = float(m.group(3)), float(m.group(4))
        sp = "гость" if m.group(5) == "ГОСТЬ" else "ведущий"
        text = m.group("text").strip()
        if o > i and text:
            chunks.append((i, o, sp, text))
    return chunks


def load_broll(path: Path) -> List[Dict]:
    """CSV из detect.py: file,plan,hero,duration,desc,conf,path."""
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            try:
                dur = float((r.get("duration") or "3").replace(",", "."))
            except ValueError:
                dur = 3.0
            rows.append({"file": (r.get("file") or "").strip(),
                         "plan": (r.get("plan") or "деталь").strip(),
                         "hero": (r.get("hero") or "перебивка").strip(),
                         "duration": dur,
                         "path": str(Path(r.get("path") or "").expanduser()
                                     if Path(r.get("path") or "").is_absolute()
                                     else (Path.cwd() / (r.get("path") or ""))),
                         "desc": (r.get("desc") or "").strip()})
    rows = [r for r in rows if r["file"]]
    # сначала детали и крупные — лучшие перебивки
    order = {"деталь": 0, "крупный": 1, "средний1": 2, "средний2": 3, "общий": 4, "дальний": 5}
    rows.sort(key=lambda r: order.get(r["plan"], 9))
    return rows


def black_scan(wide: Path, gcam: Path, cache_dir: Path) -> Dict[str, list]:
    """Где в каждой камере нет картинки. Результат кэшируется на диске."""
    iv = {}
    for key, path in (("wide", wide), ("gcam", gcam)):
        if not Path(path).is_file():
            continue
        found = blackframes.black_intervals(
            path, cache_dir=cache_dir,
            min_dur=float(settings.get("black.min_dur_sec", 0.5)),
            pix_th=float(settings.get("black.black_ratio", 0.10)),
            sample_fps=float(settings.get("black.sample_fps", 2.0)))
        if found:
            black = sum(b - a for a, b in found)
            print(f"  {key}: без картинки {black:.1f}с в {len(found)} "
                  f"участках — минуем их")
        iv[key] = found
    return iv


def plan_cameras(chunks, wide: Path, gcam: Path, cache_dir: Path):
    """К каждому куску решаем, какая камера основная.

    Камеру выбирает говорящий, но если она молчит картинкой, а вторая
    снимает - берём вторую: выбрасывать кусок из-за одной мёртвой камеры
    обидно. Выбрасываем только когда не снимает никто.
    """
    enabled = bool(settings.get("black.enabled", True))
    ratio = float(settings.get("black.drop_ratio", 0.8))
    swap = bool(settings.get("black.swap_to_alt", True))
    iv = black_scan(wide, gcam, cache_dir) if enabled else {}
    planned, dropped, swapped = [], 0, 0
    for (i, o, sp, text) in chunks:
        want = "gcam" if sp == "гость" else "wide"
        other = "wide" if want == "gcam" else "gcam"
        if iv:
            want_black = blackframes.is_black(iv.get(want, []), i, o, ratio)
            if want_black:
                if blackframes.is_black(iv.get(other, []), i, o, ratio) \
                        or not swap:
                    dropped += 1
                    continue
                want, swapped = other, swapped + 1
        planned.append((i, o, sp, text, want))
    if dropped or swapped:
        print(f"Камера: выброшено {dropped} кусков без картинки, "
              f"у {swapped} взят запасной ракурс.")
    return planned, dropped, swapped


def run_from_transcript(args) -> int:
    """Сборка selects из правленого transcript.txt (второй проход:
    текст уже вычищен человеком). Видео нужны для путей, микрофоны — нет."""
    out = Path(args.outdir)
    wide = Path(args.wide)
    gcam = Path(args.guest_cam)
    if not wide.is_file() or not gcam.is_file():
        print("Для сборки нужны --wide и --guest-cam (пути в selects).")
        return 2
    chunks = load_edited_transcript(Path(args.from_transcript))
    if not chunks:
        print("В файле не осталось строк — нечего собирать.")
        return 1
    gw, hw = out / "_guest.wav", out / "_host.wav"
    has_mics = gw.is_file() and hw.is_file()
    if not has_mics:
        print("Микрофонов _guest/_host.wav нет — звук в A1 пойдёт с камер.")
    broll = load_broll(Path(args.broll)) if args.broll and Path(args.broll).is_file() else []

    CAMS = {"wide": (wide, args.wide_plan, "ведущий"),
            "gcam": (gcam, args.guest_plan, "гость")}
    chunks, _, _ = plan_cameras(chunks, wide, gcam, out)
    if not chunks:
        print("После отсева чёрного не осталось ни одного куска.")
        return 1

    rows = []
    bi = 0
    for k, (i, o, sp, text, camkey) in enumerate(chunks):
        vf, vplan, vhero = CAMS[camkey]
        avf = CAMS["gcam" if camkey == "wide" else "wide"][0]
        tag = "ГОСТЬ" if sp == "гость" else "ВЕДУЩИЙ"
        row = {"file": vf.name, "plan": vplan, "hero": vhero,
               "duration": round(o - i, 2), "in": round(i, 2), "out": round(o, 2),
               "desc": f"{tag}: {text[:110]}", "path": str(vf.absolute()),
               "alt": avf.name, "alt_path": str(avf.absolute())}
        if has_mics:
            row["audio"] = str(gw.absolute()) if sp == "гость" else str(hw.absolute())
        rows.append(row)
        if k < len(chunks) - 1 and broll:
            b = broll[bi % len(broll)]
            bi += 1
            rows.append({"file": b["file"], "plan": b["plan"], "hero": b["hero"],
                         "duration": args.cover_len, "in": "", "out": "",
                         "desc": f"ПЕРЕБИВКА после {o:.1f}с", "path": b["path"]})
    with open(out / "selects.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["file", "plan", "hero", "duration", "in", "out",
                                          "desc", "path", "audio", "alt", "alt_path"],
                           restval="", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    with open(out / "speech.json", "w", encoding="utf-8") as f:
        import json
        json.dump([{"start": round(i, 2), "end": round(o, 2)}
                   for i, o, _, _, _ in chunks], f)
    total = sum(o - i for i, o, _, _, _ in chunks)
    print(f"Собрано из правленого транскрипта: {len(chunks)} кусков, {total:.1f}с -> {out/'selects.csv'}")
    print(f"Дальше: premiere.py --timeline {out/'selects.csv'} --media-dir . --out ryba.xml --trim-rec")
    return 0


def run_multicam(args) -> int:
    """Мультикамера: 2 видео + 2 микрофона, всё на общей шкале (sync.py)."""
    if args.from_transcript:
        if not Path(args.from_transcript).is_file():
            print(f"Нет файла транскрипта: {args.from_transcript}")
            return 2
        return run_from_transcript(args)
    out = Path(args.outdir)
    wide = Path(args.wide)
    gcam = Path(args.guest_cam)
    gmic = Path(args.guest_mic)
    hmic = Path(args.host_mic)
    for p, what in ((wide, "--wide"), (gcam, "--guest-cam"),
                    (gmic, "--guest-mic"), (hmic, "--host-mic")):
        if not p.is_file():
            print(f"Нет файла ({what}): {p}")
            print("Сначала прогони sync.py — мультикамера ест только синхрон.")
            return 2
    if args.broll and not Path(args.broll).is_file():
        print(f"Нет CSV перебивок: {args.broll}. Продолжаю без прикрытия.")
        args.broll = ""
    out.mkdir(parents=True, exist_ok=True)

    print("1/5 Аудио микрофонов...")
    gw, hw = out / "_guest.wav", out / "_host.wav"
    extract_wav(gmic, gw)
    extract_wav(hmic, hw)
    audio_g, audio_h = load_mono16(gw), load_mono16(hw)
    n = min(len(audio_g), len(audio_h))
    audio_g, audio_h = audio_g[:n], audio_h[:n]

    print("2/5 Транскрибация двух каналов...")
    model, dev = make_model(args.model, args.device)
    print(f"Транскрибация на {dev.upper()}...")
    words_g, _ = transcribe_audio_array(audio_g, model, "гость")
    words_h, _ = transcribe_audio_array(audio_h, model, "ведущий")
    if not words_g and not words_h:
        print("Речи нет ни в одном микрофоне.")
        return 1

    print("3/5 Мусор, паузы, дележка перекрытий...")
    n_low = drop_lowprob(words_g + words_h, args.min_prob)
    auto_g, cover_g, man_g = mark_trash(words_g)
    auto_h, cover_h, man_h = mark_trash(words_h)
    kept_g = [w for w in words_g if not w.drop]
    kept_h = [w for w in words_h if not w.drop]
    # реплики целиком отдаём более громкому микрофону (дубли в двух
    # микрофонах умирают здесь, а не расползаются по таймлайну)
    n_over, ambig = group_utterances(kept_g, kept_h, audio_g, audio_h,
                                     ambig_db=args.ambig_db)
    kept_g = [w for w in kept_g if not w.drop]
    kept_h = [w for w in kept_h if not w.drop]

    def is_ambig(i, o):
        return any(a < o and i < b for a, b in ambig)
    # фразовый мусор: с прикрытием режем, без — в ручной список
    broll = load_broll(Path(args.broll)) if args.broll else []
    covered, left_manual = [], [(w, "гость") for w in man_g] + [(w, "ведущий") for w in man_h]
    for w in cover_g + cover_h:
        if w.drop:
            continue
        if broll:
            w.drop, w.why = True, "фраза под перебивку"
            covered.append(w)
        else:
            left_manual.append((w, "гость" if w in cover_g else "ведущий"))
    kept_g = [w for w in words_g if not w.drop]
    kept_h = [w for w in words_h if not w.drop]

    chunks, cut_pauses = build_multicam_chunks(kept_g, kept_h, args.merge_gap,
                                               args.max_pause, args.min_keep)
    if not chunks:
        print("После чисток ничего не осталось — ослабь --min-keep.")
        return 1

    n_lone = sum(1 for w in words_g + words_h if w.why == "огрызок")
    write_numbered_transcript(out / "transcript.txt", chunks)
    if args.transcript_only:
        print(f"Транскрипт: {out/'transcript.txt'} ({len(chunks)} кусков, огрызков снято: {n_lone}).")
        print("Вычеркни мусор строками с '-' в начале, потом:")
        print(f"  plugin/src/modes/interview.py --wide ... --guest-cam ... --outdir {out} "
              f"--from-transcript {out/'transcript.txt'}")
        return 0

    CAMS = {"wide": (wide, args.wide_plan, "ведущий"),
            "gcam": (gcam, args.guest_plan, "гость")}
    chunks, n_black, n_swap = plan_cameras(chunks, wide, gcam, out)

    def susp(text):
        # короткие куски — под вопросом: программа их оставляет, но метит
        return len(text.split()) <= 2

    rows = []
    bi = 0
    susp_list = []
    for k, (i, o, sp, text, camkey) in enumerate(chunks):
        vf, vplan, vhero = CAMS[camkey]
        tag = "ГОСТЬ" if sp == "гость" else "ВЕДУЩИЙ"
        desc = f"{tag}: {text[:110]}"
        if susp(text):
            desc = "[?] " + desc
            susp_list.append((i, o, sp, text))
        if is_ambig(i, o):
            desc = "[~] " + desc
            susp_list.append((i, o, sp, text + " (микрофоны слышат одинаково)"))
        avf = CAMS["gcam" if camkey == "wide" else "wide"][0]
        rows.append({"file": vf.name, "plan": vplan, "hero": vhero,
                     "duration": round(o - i, 2), "in": round(i, 2), "out": round(o, 2),
                     "desc": desc, "path": str(vf.absolute()),
                     "audio": str(gw.absolute()) if sp == "гость" else str(hw.absolute()),
                     "alt": avf.name, "alt_path": str(avf.absolute())})
        if k < len(chunks) - 1 and broll:
            b = broll[bi % len(broll)]
            bi += 1
            rows.append({"file": b["file"], "plan": b["plan"], "hero": b["hero"],
                         "duration": args.cover_len, "in": "", "out": "",
                         "desc": f"ПЕРЕБИВКА после {o:.1f}с", "path": b["path"]})
    with open(out / "selects.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["file", "plan", "hero", "duration", "in", "out",
                                          "desc", "path", "audio", "alt", "alt_path"],
                           restval="", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    write_numbered_transcript(out / "transcript.txt", chunks)

    with open(out / "speech.json", "w", encoding="utf-8") as f:
        import json
        json.dump([{"start": round(i, 2), "end": round(o, 2)}
                   for i, o, _, _, _ in chunks], f)

    tg = sum(o - i for i, o, sp, _, _ in chunks if sp == "гость")
    th = sum(o - i for i, o, sp, _, _ in chunks if sp == "ведущий")
    n_joints = max(0, len(chunks) - 1)
    report = [
        f"Рыба интервью (мультикамера): {wide.name} + {gcam.name}",
        f"Гость: {tg:.1f}с, ведущий: {th:.1f}с. Кусков: {len(chunks)}.",
        f"Дублей в чужом микрофоне снято: {n_over}. Огрызков (слов-одиночек): {n_lone}.",
        f"Неуверенных слов (< {args.min_prob}): {n_low}.",
        f"Пауз ужато: {cut_pauses:.1f}с.",
        f"Паразитов сразу: {len(auto_g) + len(auto_h)}. Фраз под перебивкой: {len(covered)}.",
        f"Стыков прикрыто: {n_joints if broll else 0} из {n_joints}."
        + ("" if broll else " (нет --broll — закрой вручную)"),
        f"Без картинки выброшено: {n_black}. Взят запасной ракурс: {n_swap}.",
        "",
        "СОМНИТЕЛЬНЫЕ (короткие — глянь, нужны ли; помечены [?] в selects):",
        *[f"  {i:.1f}-{o:.1f} [{sp}] «{text[:80]}»" for i, o, sp, text, _ in
          [(i, o, sp, t, c) for i, o, sp, t, c in chunks if len(t.split()) <= 2][:20]],
        "",
        "ВРУЧНУЮ:",
        *[f"  {w.start:.1f}с [{sp}] «{w.w}»" for w, sp in left_manual[:40]],
        "",
        "Точнее всего — правка текста: вычеркни строки с '-' в transcript.txt и собери заново:",
        f"  interview.py --wide {wide} --guest-cam {gcam} --outdir {out} "
        f"--from-transcript {out/'transcript.txt'}",
        "",
        "Звук в Premiere: в A1 уже лежат микрофоны (гость/ведущий по кускам).",
        f"  python plugin/src/io/premiere.py --timeline {out/'selects.csv'} --media-dir . --out ryba.xml --trim-rec",
    ]
    (out / "report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report[:6]))
    print(f"\nГотово: {out} (selects.csv, transcript.txt, speech.json, report.txt)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AutoCut Interview — рыба по тексту")
    ap.add_argument("--input", default="", help="файл интервью (видео, одиночный режим)")
    ap.add_argument("--broll", default="", help="CSV перебивок из detect.py")
    ap.add_argument("--outdir", default="rz", help="папка результата")
    ap.add_argument("--model", default=settings.get("interview.model", "base"), help="модель whisper: tiny/base/small")
    ap.add_argument("--device", default=settings.get("interview.device", "auto"), help="auto|cpu|cuda (auto: GPU если завелась)")
    ap.add_argument("--max-pause", type=float,
                    default=float(settings.get("interview.max_pause_sec", 0.6)), help="паузы длиннее — ужать до этого")
    ap.add_argument("--merge-gap", type=float,
                    default=float(settings.get("interview.merge_gap_sec", 0.8)), help="стык кусков при разрыве больше")
    ap.add_argument("--min-keep", type=float,
                    default=float(settings.get("interview.min_keep_sec", 1.0)), help="куски короче — выбросить")
    ap.add_argument("--cover-len", type=float,
                    default=float(settings.get("interview.cover_len_sec", 1.6)), help="длина вставки-перебивки")
    ap.add_argument("--plan", default="средний1", help="план камеры интервью")
    ap.add_argument("--hero", default="гость", help="кто говорит")
    ap.add_argument("--wide", default="", help="МУЛЬТИКАМЕРА: общий план (ведущий+гость, синхрон)")
    ap.add_argument("--guest-cam", default="", help="МУЛЬТИКАМЕРА: камера гостя (синхрон)")
    ap.add_argument("--guest-mic", default="", help="МУЛЬТИКАМЕРА: микрофон гостя (синхрон)")
    ap.add_argument("--host-mic", default="", help="МУЛЬТИКАМЕРА: микрофон ведущего (синхрон)")
    ap.add_argument("--wide-plan", default="общий", help="план широкой камеры")
    ap.add_argument("--guest-plan", default="средний1", help="план камеры гостя")
    ap.add_argument("--min-prob", type=float,
                    default=float(settings.get("interview.min_word_prob", 0.25)),
                    help="слова с уверенностью ниже — вон (проникновение/гул)")
    ap.add_argument("--ambig-db", type=float,
                    default=float(settings.get("interview.ambiguous_db", 3.0)),
                    help="микрофоны слышат с разницей меньше — пометить [~]")
    ap.add_argument("--transcript-only", action="store_true",
                    help="только транскрипт с номерами (правка текста до сборки)")
    ap.add_argument("--from-transcript", default="",
                    help="собрать selects из правленого transcript.txt (мультикамера)")
    args = ap.parse_args(argv)

    if args.wide or args.guest_mic or args.host_mic:
        return run_multicam(args)

    src = Path(args.input)
    out = Path(args.outdir)
    if not src.is_file():
        print(f"Не найден файл интервью: {src}")
        print("Укажи реальный путь, например: --input \"E:\\Видео\\интервью.mp4\"")
        print("Если в пути пробелы — кавычки обязательны.")
        return 2
    if args.broll and not Path(args.broll).is_file():
        print(f"Не найден CSV перебивок: {args.broll} (сначала прогони detect.py). Продолжаю без прикрытия стыков.")
        args.broll = ""
    out.mkdir(parents=True, exist_ok=True)

    wav = out / "_speech.wav"
    print("1/5 Аудио...")
    extract_wav(src, wav)
    print("2/5 Транскрибация (первый раз качает модель)...")
    words, sents = transcribe(wav, args.model, args.device)
    if not words:
        print("Речи не нашлось. Проверь файл.")
        return 1

    print("3/5 Мусор и паузы...")
    drop_lowprob(words, args.min_prob)
    auto_drop, need_cover, manual = mark_trash(words)
    chunks, cut_pauses = build_keeps(words, args.max_pause, args.merge_gap, args.min_keep)

    broll = load_broll(Path(args.broll)) if args.broll else []
    if args.broll and not broll:
        print(f"В {args.broll} нет строк — стыки прикрою списком вручную.")

    # фразовый мусор: с прикрытием — режем и вставляем перебивку поверх стыка,
    # без прикрытия — оставляем и отдаём в ручной список
    covered, left_manual = [], list(manual)
    for w in need_cover:
        if w.drop:
            continue
        if broll:
            w.drop, w.why = True, "фраза под перебивку"
            covered.append(w)
        else:
            left_manual.append(w)
    if covered:
        chunks, cut2 = build_keeps(words, args.max_pause, args.merge_gap, args.min_keep)
        cut_pauses += cut2

    total_src = words[-1].end - words[0].start
    kept_dur = sum(o - i for i, o, _ in chunks)

    # 4/5 selects.csv c точными in/out
    rows = []
    bi = 0
    for k, (i, o, text) in enumerate(chunks):
        rows.append({"file": src.name, "plan": args.plan, "hero": args.hero,
                     "duration": round(o - i, 2), "in": round(i, 2), "out": round(o, 2),
                     "desc": text[:120], "path": str(src.absolute())})
        # перебивка между кусками одного говорящего (прячет jump cut)
        if k < len(chunks) - 1 and broll:
            b = broll[bi % len(broll)]
            bi += 1
            rows.append({"file": b["file"], "plan": b["plan"], "hero": b["hero"],
                         "duration": args.cover_len, "in": "", "out": "",
                         "desc": f"ПЕРЕБИВКА после {o:.1f}с", "path": b["path"]})
    with open(out / "selects.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["file", "plan", "hero", "duration", "in", "out", "desc", "path"])
        w.writeheader()
        w.writerows(rows)

    with open(out / "transcript.txt", "w", encoding="utf-8") as f:
        for s in sents:
            f.write(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}\n")

    # speech-интервалы для дакинга музыки (только речь, уже после чисток)
    with open(out / "speech.json", "w", encoding="utf-8") as f:
        import json
        json.dump([{"start": round(i, 2), "end": round(o, 2)} for i, o, _ in chunks], f)

    n_joints = max(0, len(chunks) - 1)
    report = [
        f"Рыба интервью: {src.name}",
        f"Речь: {total_src:.1f}с -> в деле {kept_dur:.1f}с ({kept_dur/max(total_src,0.01)*100:.0f}%).",
        f"Пауз ужато: {cut_pauses:.1f}с. Кусков: {len(chunks)}.",
        f"Паразитов вырезано сразу: {len(auto_drop)} ({Counter(w.why for w in auto_drop)}).",
        f"Фраз под перебивкой: {len(covered)}." if broll else "Фраз под перебивку: 0 (нет --broll).",
        f"Стыков прикрыто перебивками: {min(n_joints, len(broll) and 10**9 or 0)} из {n_joints}."
        if broll else f"Стыков БЕЗ прикрытия: {n_joints} — закрой J/L-cut или перебивкой вручную.",
        "",
        "ВРУЧНУЮ (длинный мусор — послушай по таймкодам):",
        *[f"  {w.start:.1f}с «{w.w}» ({w.end-w.start:.2f}с)" for w in left_manual[:40]],
        "",
        "Дальше:",
        f"  python plugin/src/io/premiere.py --timeline {out/'selects.csv'} --media-dir . --out ryba.xml --trim-rec",
        "  (точные куски --trim-rec не трогает; перебивки ужмёт по норме плана)",
    ]
    (out / "report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report[:8]))
    print(f"\nГотово: {out} (selects.csv, transcript.txt, speech.json, report.txt)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

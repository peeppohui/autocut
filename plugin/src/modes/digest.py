#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Дайджест: стендап, нарезка, интервью -> одна секвенция.

Дайджест состоит из трёх частей, и они обрабатываются по-разному, потому
что разные по смыслу (разбор с замерами в AGENTS.md):

* **стендап** — корреспондент читает текст в кадр. Файлов много, это
  дубли одного выступления. Берём ЛУЧШИЙ дубль и вырезаем запинки.
* **нарезка** — событие. Короткие клипы, говорящего нет. Порядок по
  шкале крупности, отсев мыла и тряски.
* **интервью** — разные люди, по файлу на человека. Из каждого берём
  один ответ секунд на 15, средний план (стандарт, слайд 12).

Звук на все части один — рекордер. Слова (Whisper) дают кандидатов и
якорь сдвига, но одинаковый текст есть у всех дублей: словесный матч
промахивался по дублю (рассинхрон в готовом ролике). Поэтому пару
камера/рекордер проверяет огибающая звука (ml/sync): сдвиг принимается
только когда звук совпадает, а не когда совпали слова.

Порядок в секвенции: стендап -> нарезка -> интервью. Сначала ведущий
объясняет, что сейчас будет, потом показываем, потом даём голос
участникам.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # plugin/src/modes
SRC = HERE.parent
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "config"))

import bootstrap            # noqa: E402,F401
import settings             # noqa: E402

import interview            # noqa: E402
import speech               # noqa: E402
from ml import describe      # noqa: E402
from ml import sorter       # noqa: E402
from ml import recmatch
from ml import sync
from ml import theme
from modes import recsound        # noqa: E402


DIGEST_PREFIX = "digest"


def cfg(key, default):
    v = settings.get(DIGEST_PREFIX + "." + key, default)
    return v if isinstance(v, (int, float, str)) else default


# ---------------------------------------------------------------- части


def resolve_parts(root: Path):
    """Папки трёх частей.

    Человек указывает папку проекта, а материалы часто лежат во вложенной
    (`материал/исх`). Ищем части в корне и на пару уровней вглубь по тому
    же правилу, что и сортировщик: иначе блок падал бы с «нет папок
    частей» при материале, который рядом. Готовый ролик в корне не
    считается материалом — части важнее.
    """
    base = sorter.find_parts_base(root)
    if base is None:
        return {k: None for k in ("standup", "broll", "interview")}
    got = sorter.parts_in(base)
    return {k: got.get(k) for k in ("standup", "broll", "interview")}


def videos(folder: Path):
    if folder is None or not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in sorter.VIDEO_EXT)


# ---------------------------------------------------------------- речь


# Граница фразы. Замер на готовом дайджесте: куски интервью там и стоят
# там, где кончилась фраза, а не там, где кончился кусок.
PHRASE_GAP = 0.45
MAX_PIECE = 4.5
MIN_PIECE = 1.2
# Тишина, отделяющая один заход речи от другого. Спикер в интервью
# говорит рывками; «лучший монолог» — самый длинный кусок без такого
# молчания. Определено здесь, а не ниже: best_monologue() берёт его
# значением по умолчанию, и константа должна быть видима к моменту
# определения функции.
MONO_GAP = 2.0
# Стендап режется крупнее: в ролике корреспондента сменяли ракурсы раз
# в 2–4 секунды, но ЗВУК шёл непрерывно. Резать один дубль на куски по
# две секунды — значит жевать слова: «6 научных фронтов» отдельным куском
# звучит как заикание. Один дубль режется только по концам фраз.
STANDUP_MAX_PIECE = 12.0


def phrase_pieces(words, max_piece=MAX_PIECE, min_piece=MIN_PIECE,
                  limit_sec=None):
    """Слова -> куски ролика, разрезанные по границам фраз.

    Куски режутся не по таймеру, а там, где кончилась фраза: обрыв
    посередине фразы зритель слышит, и монтаж выдаёт себя. Границы фразы
    — пауза длиннее PHRASE_GAP или конец предложения по знаку.

    Фраза длиннее max_piece режется ровно: длинную мысль всё равно не
    вырезать посередине, а в эфире её читают в темп. Текст каждого
    куска соответствует именно этому куску, а не всей фразе: в плане
    монтажёр видит, что именно здесь говорят.
    """
    if not words:
        return []
    groups = []
    cur = []
    prev_end = None
    prev_ends_sentence = False
    for w, t0, t1 in words:
        if prev_end is not None:
            gap = t0 - prev_end
            # Новая фраза начинается после паузы или после слова, которое
            # закончило предложение. Знак смотрим у ПРЕДЫДУЩЕГО слова:
            # иначе «четыре.» уезжало бы в следующий кусок и фраза
            # «раз два три четыре» рассыпалась.
            if gap > PHRASE_GAP or prev_ends_sentence:
                # Граничное слово начинает СЛЕДУЮЩУЮ группу. Если
                # закрыть им текущую, кусок растянется на паузу и
                # следующий начнётся с её середины.
                if cur:
                    groups.append(cur)
                cur = [(w, t0, t1)]
            else:
                cur.append((w, t0, t1))
        else:
            cur.append((w, t0, t1))
        prev_ends_sentence = w.endswith((".", "!", "?", "…"))
        prev_end = t1
    if cur:
        groups.append(cur)

    # Короткие фразы приклеиваем к предыдущей: обрыв в одно слово —
    # это не фраза, а обрывок. Запас 0.05с — из-за float-шума фраза
    # ровно в 1.2с считалась короткой (1.1999999999999997 < 1.2) и
    # склеивалась обратно.
    merged = []
    for g in groups:
        span = g[-1][2] - g[0][1]
        if merged and span < min_piece - 0.05:
            merged[-1] = merged[-1] + g
        else:
            merged.append(list(g))
    groups = [g for g in merged if g[-1][2] - g[0][1] >= 0.8]

    # Предел длины применяем ПОСЛЕ склейки. Склейка коротких кусков
    # давала куски по 11 секунд, и интервью не добирало свою долю
    # хронометража: вместо трёх кусков по четыре секунды выходил один
    # на одиннадцать.
    pieces = []
    for g in groups:
        start = g[0][1]
        chunk = []
        for wd in g:
            if chunk and wd[2] - start > max_piece:
                pieces.append((start, chunk[-1][2], chunk))
                start = chunk[-1][2]
                chunk = []
            chunk.append(wd)
        if chunk:
            pieces.append((start, chunk[-1][2], chunk))

    out = []
    for a, b, chunk in pieces:
        if b - a < 0.8:
            # Обрывок меньше секунды в таймлайне — вспышка. Монтажёр
            # такое видит как ошибку, а в ролике это просто мельтешение.
            continue
        txt = " ".join(x[0].strip(" ,.;:!?-–—\"«»") for x in chunk)
        out.append((a, b, txt, chunk))

    if limit_sec:
        keep = []
        total = 0.0
        for p in out:
            if total + (p[1] - p[0]) > limit_sec and keep:
                break
            keep.append(p)
            total += p[1] - p[0]
        out = keep
    return out


def speak_words(video: Path, work: Path, model_name, device):
    """Слова в файле: [(слово, начало, конец)] либо [], если речи нет."""
    try:
        return speech.words_of(video, work, model_name, device)
    except Exception as e:
        print("  {}: не разобрать речь ({})".format(video.name, str(e)[:90]))
        return []


def longest_run(words, gap=PHRASE_GAP):
    """Длиннейший кусок речи без единого разрыва, в секундах.

    Считается по словам: разрыв длиннее gap разрывает кусок. Из
    этого и выбирается дубль стендапа — в эфир идёт связная речь подряд,
    и дубль, разбитый на три обрывка по 8 секунд, читается как «человек
    не знает, что сказать», даже если суммарно он длиннее дубля с одной
    связной фразой на 30 секунд.
    """
    if not words:
        return 0.0
    best = cur = 0.0
    prev_end = None
    for _w, t0, t1 in words:
        if prev_end is not None and t0 - prev_end > gap:
            cur = 0.0
        cur += (t1 - t0)
        if prev_end is not None and t0 - prev_end <= gap:
            cur += (t0 - prev_end)
        prev_end = t1
        best = max(best, cur)
    return best


def pick_best_take(candidates, rec_index=None):
    """Из дублей стендапа выбрать лучший.

    candidates: [(файл, слова)].

    **Если рядом лежат файлы рекордера, решает рекордер.** Микрофон
    стоял на рекордере, и «официальное» чтение — то, что попало в него.
    Дубль, который обрывается на полуслове, рекордеру не соответствует
    и в эфир не годится. Проверено на материале: C0102 (чтение
    оборвано на «...студента технического вуза») рекордеру не
    соответствует, а C0109 — соответствует (качество 0.94).

    Возвращает (rank, файл, слова, сумма речи) или None.
    """
    best = None
    for name, words in candidates:
        if not words:
            continue
        longest = longest_run(words)
        total = sum(t1 - t0 for _w, t0, t1 in words)
        pieces = phrase_pieces(words)
        worst = min(interview.chunk_score(i, o, t) for i, o, t, _c in pieces) \
            if pieces else 0
        rec_q = 0.0
        if rec_index:
            _rn, _off, rec_q = recmatch.find(words, rec_index)
        rank = (rec_q, longest, total, -worst)
        if best is None or rank > best[0]:
            best = (rank, name, words, total)
    return best


def process_standup(folder: Path, work: Path, model_name, device, log,
                    budget=None, rec_index=None):
    """Лучший дубль стендапа -> [(файл, in, out, текст, слова)].

    ОДИН кусок, непрерывный звук. В готовом дайджесте стендап шёл
    21.7 секунды непрерывной речи, а картинка менялась каждые 2–4 с
    (C0101 -> FX20032 -> C0109 -> C0102): редактор менял РАКУРС, а не
    резал речь. Резать один дубль на куски по две секунды — значит
    жевать слова: «6 научных фронтов» отдельным куском звучит как
    заикание.

    Поэтому берём один связный кусок речи. Если речь в файле идёт
    несколькими заходами (запись с дублями), берём самый длинный
    заход, а если он длиннее бюджета — обрезаем по последнему слову,
    чтобы не разрезать фразу пополам.
    """
    files = videos(folder)
    if not files:
        return []
    target = float(cfg("standup_target_sec", 30.0))
    if budget:
        # Не дать стендапу съесть больше своей доли: он голос, а не
        # весь ролик.
        target = min(target, budget)

    log("Стенап: дублей {}".format(len(files)))
    cands = []
    for p in files:
        words = speak_words(p, work, model_name, device)
        if words:
            cands.append((p, words))
    best = pick_best_take(cands, rec_index)
    if not best:
        log("Стенап: ни один дубль не дал разборчивой речи")
        return []
    _rank, path, words, total = best

    run = best_run(words, target)
    if run is None:
        log("Стенап: речь не разбилась на связные заходы")
        return []
    i, o, text, chunk = run
    log("Стенап: {} из {} дублей, речи {:.0f}с, берём один кусок {:.1f}с"
        .format(path.name, len(files), total, o - i))
    return [(path, i, o, text, chunk)]


def best_run(words, budget):
    """Самый длинный связный заход речи, влезающий в бюджет.

    Возвращает (начало, конец, текст, слова) или None. Границы — по
    словам, чтобы не разрезать фразу.
    """
    if not words:
        return None
    runs = []
    cur = [words[0]]
    for w in words[1:]:
        if w[1] - cur[-1][2] > 1.2:
            runs.append(cur)
            cur = [w]
        else:
            cur.append(w)
    runs.append(cur)
    best = None
    for run in runs:
        span = run[-1][2] - run[0][1]
        if span <= budget and (best is None or span > best[0]):
            best = (span, run)
    if best is None:
        # Ни один заход не влез: берём первый и режем по последнему
        # слову, которое в бюджет влезает.
        run = runs[0]
        cut = [w for w in run if w[2] - run[0][1] <= budget]
        if not cut:
            return None
        run = cut
    run = best[1] if best else run
    i = run[0][1]
    o = run[-1][2]
    text = " ".join(w for w, _s, _e in run)
    return (i, o, text, list(run))


def best_monologue(words, gap=MONO_GAP):
    """Лучший заход речи: самый длинный кусок без длинного молчания.

    Спикер в интервью говорит рывками: короткая реплика, пауза, снова
    речь. Весь файл в эфир не годится — получается «набор кадров», и
    интервью в таймлайне теряется. Берём один заход, где спикер дольше
    всего говорит, и режем его на фразы: паузы внутри монолога
    вырезаются, зритель слышит связную речь.
    """
    if not words:
        return []
    runs = []
    cur = []
    prev_end = None
    for w in words:
        if prev_end is not None and w[1] - prev_end > gap:
            if cur:
                runs.append(cur)
            cur = []
        cur.append(w)
        prev_end = w[2]
    if cur:
        runs.append(cur)

    def speech(run):
        return sum(max(0.0, e - s) for _w, s, e in run)

    return max(runs, key=lambda r: (speech(r), r[-1][2] - r[0][1]))


def process_interviews(folder: Path, work: Path, model_name, device, log,
                       budget=None):
    """Ответы спикеров, нарезанные по фразам.

    Возвращает [(файл, [(in, out, текст), ...])] — по спикеру один
    список кусков. Редактор режет интервью по границам фраз: кусок
    «На решение отводится час» (1.16 с) стоит отдельно и не
    обрезается посередине. Поэтому кусков много и они короткие.

    Из файла берётся не начало, а ЛУЧШИЙ МОНОЛОГ спикера — самый
    длинный заход речи без длинного молчания. Начало файла может быть
    разгоном или ответом на чужой вопрос, и тогда в эфир уходит огрызок.
    """
    files = videos(folder)
    if not files:
        return []
    per = float(cfg("interview_answer_sec", 30.0))
    if budget and files:
        # На всех спикеров делим остаток поровну, но не меньше половины
        # ответа: обрезанный на 5с ответ в эфире не читается.
        per = max(per * 0.5, min(per, budget / len(files)))
    log("Интервью: спикеров {}".format(len(files)))
    out = []
    for p in files:
        words = speak_words(p, work, model_name, device)
        if not words:
            log("  {}: речи нет, пропускаю".format(p.name))
            continue
        mono = best_monologue(words)
        if not mono:
            continue
        pieces = phrase_pieces(mono, limit_sec=per)
        if not pieces:
            continue
        log("  {}: монолог {:.0f}с, {} кусков по фразам, {:.0f}с".format(
            p.name, mono[-1][2] - mono[0][1], len(pieces),
            sum(o - i for i, o, _t, _c in pieces)))
        out.append((p, pieces, words))
    return out


# ---------------------------------------------------------------- нарезка


def broll_csv(folder: Path, out_csv: Path, log, device="auto",
              model_name="yolov8n-pose.pt"):
    """Крупности нарезки -> CSV, который дальше разложит autocut.

    Всю работу (крупность, отсев мыла и тряски по медиане папки) делает
    `detect.py` — он уже это умеет. Свою копию этой логики заводить
    незачем: разъедутся настройки, и дайджест начнёт отбирать кадры иначе,
    чем обычная нарезка. Здесь только запуск.

    Разбор переиспользуется, если ни файлы, ни настройки не менялись:
    иначе каждое «Собрать дайджест» в окне заново прогоняет 110 клипов.
    """
    import subprocess
    files = videos(folder)
    if not files:
        log("Нарезка: папка пуста")
        return None
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    key = {"v": 1, "model": model_name, "files": folder_sig(folder),
           "cfg": settings_sig(DETECT_KEYS)}
    if is_fresh(out_csv, key):
        log("Нарезка: разбор не изменился, беру готовый")
        return out_csv
    cmd = [sys.executable, "-m", "src.ml.detect", "--input", str(folder),
           "--output", str(out_csv), "--device", device]
    log("Нарезка: разбираю {} клипов".format(len(files)))
    r = subprocess.run(cmd, cwd=str(SRC.parent),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        log("Нарезка: detect.py упал (код {})".format(r.returncode))
        for line in (r.stderr or r.stdout or "").splitlines()[-6:]:
            log("   " + line)
        return None
    try:
        with out_csv.open(encoding="utf-8") as f:
            n = len(list(csv.DictReader(f)))
    except Exception:
        n = 0
    log("Нарезка: отобрано клипов {}".format(n))
    mark_fresh(out_csv, key)
    return out_csv


# Длина вырезки из клипа нарезки. Замер на готовом дайджесте: редактор
# ставил куски по 1.2-2.6 с, а сами клипы по 5-13 с. Кусок нарезки в
# ролике — это НЕ клип целиком, а короткая вырезка из него.
BROLL_PIECE_MIN = 1.2
# Потолок вырезки нарезки. Замер на готовом дайджесте: 32 куска, от
# 0.72 до 4.16 с, в среднем 1.73. Ровные куски одной мерой смотрятся
# как слайдшоу, а не как монтаж.
BROLL_PIECE_MAX = 2.6


def pick_excerpt(words, dur, want=BROLL_PIECE_MAX):
    """Где в клипе взять вырезку: там, где в кадре говорят.

    В микрофон нарезки попали слова с мероприятия, и в те моменты, где
    что-то говорили, кадр содержательный. В тишине берём середину
    клипа: выбирать нечего, а середина обычно чище краёв.
    """
    if dur <= want:
        return (0.0, float(dur))
    if words:
        best = None
        for i, (_w, t0, _t1) in enumerate(words):
            j = i
            while j + 1 < len(words) and words[j + 1][1] - words[i][1] < want:
                j += 1
            span = words[j][2] - words[i][1]
            if best is None or span > best[0]:
                best = (span, words[i][1], words[j][2])
        if best:
            return (max(0.0, best[1]), min(float(dur), best[2]))
    mid = dur / 2.0
    return (max(0.0, mid - want / 2.0), min(float(dur), mid + want / 2.0))


# Меры вырезки нарезки. В готовом дайджесте куски шли разной длины
# (0.72…4.16 с), и ровные куски одной мерой читаются как слайдшоу.
BROLL_MEASURES = (1.2, 1.55, 1.9, 2.25, 2.6)


def broll_segments(folder: Path, labels, log, keep=None, plans=None,
                   piece_min=BROLL_PIECE_MIN, piece_max=BROLL_PIECE_MAX):
    """Клипы нарезки -> короткие вырезки [(файл, in, out, подпись, план)].

    Каждый клип даёт одну вырезку: повторять один и тот же кадр дважды
    подряд — плохо, а резать клип на три куска тоже нельзя, монтажёр
    потом не найдёт, откуда кусок.

    `keep` — клипы, прошедшие отсев мыла и тряски (список из detect.py).
    `plans` — крупность и кто в кадре: по ним отбираются кадры, чтобы
    подряд не шло три одинаковых плана.
    """
    out = []
    for k, p in enumerate(videos(folder)):
        if keep is not None and p.name not in keep:
            continue
        lab = labels.get(p.name) or {}
        words = lab.get("words") or []
        dur = float(lab.get("dur") or 0.0)
        if not dur:
            dur = clip_duration(p)
        if dur < piece_min:
            continue
        # Мера вырезки — по порядку клипов, а не одна на всех: иначе
        # все куски нарезки идут одной длиной и смотрятся как слайдшоу.
        want = min(piece_max, BROLL_MEASURES[k % len(BROLL_MEASURES)])
        a, b = pick_excerpt(words, dur, want)
        if b - a < min(piece_min, dur):
            a, b = 0.0, dur
        if b - a > piece_max:
            b = a + piece_max
        text = (lab.get("text") or "").strip()
        if not text:
            # Клип без речи: темы у него нет, и монтажёр должен видеть
            # это в плане, а не гадать, почему описание пустое.
            text = "(без речи)"
        plan = hero = ""
        if plans and p.name in plans:
            plan, hero = plans[p.name]
        out.append((p, a, b, text, plan, hero))
    return out


def clip_duration(path: Path) -> float:
    """Длительность клипа в секундах."""
    import subprocess
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


# ---------------------------------------------------------------- сборка


# Ключи настроек, которые меняют результат шага. Если человек поправил
# любой из них, готовый результат переиспользовать нельзя: он посчитан
# по другим числам. Список ведётся вручную — забыть ключ значит тихо
# отдать старый разбор под новыми настройками.
DETECT_KEYS = (
    "detect.face_w_detail", "detect.face_w_closeup", "detect.person_h_distant",
    "detect.person_h_general", "detect.person_min_body", "detect.kpt_conf",
    "detect.low_confidence", "detect.sparse_skeleton_confidence",
    "detect.min_visible_keypoints", "detect.detect_conf",
    "detect.frames_per_file", "detect.crowd_people_min",
    "detect.crowd_body_max", "detect.crowd_back_people_min",
    "detect.person_h_inframe_min", "detect.person_h_fragment",
    "detect.person_h_single_point", "detect.person_h_face_only",
    "detect.model", "sharp.enabled", "sharp.floor_factor", "sharp.shake_max",
)


def folder_sig(folder: Path):
    """Подпись папки: имя, размер и время каждого видео."""
    sig = {}
    for p in sorted(videos(folder)):
        st = p.stat()
        sig[p.name] = [int(st.st_size), int(st.st_mtime_ns)]
    return sig


def settings_sig(keys):
    return {k: settings.get(k) for k in keys}


def _key_id(key) -> str:
    blob = json.dumps(key, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _sidecar(cache: Path) -> Path:
    return cache.with_name(cache.name + ".key")


def is_fresh(cache: Path, key) -> bool:
    """Готовый результат ещё годится: посчитан ровно по этим входам."""
    side = _sidecar(cache)
    if not cache.is_file() or not side.is_file():
        return False
    try:
        return side.read_text(encoding="utf-8").strip() == _key_id(key)
    except Exception:
        return False


def mark_fresh(cache: Path, key) -> None:
    try:
        _sidecar(cache).write_text(_key_id(key), encoding="utf-8")
    except Exception:
        pass


def write_plan(rows, out_csv: Path, base: Path):
    """rows: [(часть, файл, in, out, текст, дорожка, позиция, звук)] -> CSV.

    Колонка path — путь файла ОТНОСИТЕЛЬНО base, того каталога,
    который пользователь указал в окне и который потом уходит в Premiere
    как media-dir. Если считать путь от папки с частями, получится
    «стендап/файл.mp4», и Premiere будет искать его прямо в корне
    проекта, где его нет: материал лежит на уровень глубже, в исх.
    Тогда Premiere просит relink на каждом клипе.

    Колонка track — дорожка секвенции (V1 голос, V2 нарезка).
    Колонка pos — где клип стоит на таймлайне, в секундах. Без неё
    дорожки идут подряд и нарезка уезжает за голосом.

    Звук (колонка audio) — либо готовый файл, либо словарь
    {"path": исходник, "in": сек, "out": сек, "duration": сек,
    "track": "A2"}. Для рекордера храним ИСХОДНЫЙ файл и его края:
    нарезанный WAV начинается с нуля, и Premiere искал бы в нём
    камерный таймкод. Длительность исходника нужна для <file><duration>.
    """
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["n", "file", "part", "path", "in", "out", "duration",
                    "desc", "track", "pos", "audio", "audio_in", "audio_out",
                    "audio_duration", "audio_track", "sound"])
        for i, row in enumerate(rows, 1):
            part, path, src_in, src_out, desc = row[:5]
            track = row[5] if len(row) > 5 else ""
            pos = row[6] if len(row) > 6 else ""
            audio = row[7] if len(row) > 7 else ""
            if isinstance(audio, dict):
                audio_path = str(audio.get("path") or "")
                audio_in = audio.get("in")
                audio_out = audio.get("out")
                audio_duration = audio.get("duration")
                audio_track = str(audio.get("track") or "")
            else:
                audio_path = str(audio or "")
                audio_in = audio_out = audio_duration = ""
                audio_track = ""
            # Колонка sound: "нет" — клип выкладывается без звукового
            # клипа вовсе (перебивки поверх речи и в паузах: звук там
            # несёт рекордер или музыка, а не шум зала вырезки).
            sound = "нет" if audio == "нет" else ""
            if sound:
                # Маркер в audio не пролезает: premiere.py по пустой
                # audio решает, что звук из видеофайла, а по sound=нет
                # вообще не создаёт звуковой клип.
                audio_path = ""
            try:
                rel = path.resolve().relative_to(base.resolve()).as_posix()
            except Exception:
                rel = path.name
            # Звук с рекордера лежит рядом с материалом, а не внутри
            # папки результата. Поэтому: внутри base — относительный,
            # снаружи — абсолютный (premiere.py абсолютные берёт как есть).
            arel = ""
            if audio_path:
                ap = Path(audio_path)
                if not ap.is_absolute():
                    ap = base / ap
                try:
                    arel = ap.resolve().relative_to(base.resolve()).as_posix()
                except Exception:
                    arel = str(ap.resolve())

            def fmt_num(value):
                if value is None or value == "":
                    return ""
                try:
                    # 4 знака: сетка 25 fps укладывается в 2 (0.04),
                    # но 30 fps требует 4 (1/30). Точность не мешает.
                    return "{:.4f}".format(float(value))
                except (TypeError, ValueError):
                    return ""

            w.writerow([i, path.name, part, rel,
                        "{:.4f}".format(src_in), "{:.4f}".format(src_out),
                        "{:.4f}".format(src_out - src_in), desc, track,
                        ("{:.4f}".format(pos) if pos != "" else ""), arel,
                        fmt_num(audio_in), fmt_num(audio_out),
                        fmt_num(audio_duration), audio_track, sound])
    return out_csv


# Музыка — фон, а не второй голос. На стендапе и интервью она должна
# уйти вниз: иначе перебивает речь (правка пользователя). Громкость в
# XML Premiere читает непредсказуемо, поэтому приглушаем сам файл —
# ffmpeg умеет менять громкость по интервалам времени.
DUCK_LEVEL = 0.30


def duck_filter(spans, level=DUCK_LEVEL):
    """Фильтр ffmpeg: приглушить музыку на интервалах речи.

    spans — [(начало, конец), ...] в секундах таймлайна.
    """
    expr = "+".join("between(t,{:.2f},{:.2f})".format(a, b)
                    for a, b in spans if b > a)
    if not expr:
        return ""
    return "volume={:.2f}:enable='{}'".format(level, expr)


def duck_music(music, rows, work, log, level=DUCK_LEVEL):
    """Приглушить музыку там, где говорит спикер.

    Исходник не трогаем: приглушённая копия ложится в work, и в плане
    на неё ссылается строка «музыка».
    """
    spans = []
    for row in rows:
        part = row[0]
        if part not in ("стендап", "интервью"):
            continue
        pos = float(row[6])
        spans.append((pos, pos + (float(row[3]) - float(row[2]))))
    if not spans:
        return None
    filt = duck_filter(spans, level)
    out = Path(work) / "music_duck.mp3"
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(music), "-af", filt,
           "-c:a", "libmp3lame", "-b:a", "192k", str(out)]
    try:
        rc = subprocess.call(cmd)
    except OSError as e:
        log("Музыка: приглушить не удалось ({})".format(e))
        return None
    if rc != 0 or not out.exists() or out.stat().st_size < 1024:
        log("Музыка: приглушить не удалось, оставляем как есть")
        return None
    return out


# Доли экранного времени. Замер на готовом дайджесте по совпавшим
# кадрам: нарезка 55.4 с из 101.7 (55%), интервью 24.6 с (24%),
# стендап 21.7 с (21%). Нарезка идёт первой по замыслу: «сначала
# ведущий объясняет, потом показываем, потом даём голос участникам» —
# и в готовом ролике показывают больше половины времени.
#
# Доля нарезки уменьшена: в прошлой сборке интервью занимало 8-9 с и
# зритель считал, что его «съели» перебивки. Голос участника — это и
# есть интервью, ему отдаём больше времени, нарезка идёт в хвост.
SHARE_STANDUP = 0.21
SHARE_BROLL = 0.45

# Дорожки сборки. В эталонном проекте голос лежит на V1, нарезка — на
# V2 отдельным слоем: Premiere показывает нарезку там, где голоса нет,
# и монтажёр гасит V1, чтобы посмотреть, что под голосом.
VOICE_TRACK = "V1"
BROLL_TRACK = "V2"
# Музыкальная подложка живёт на своей дорожке A3: звук без картинки.
# Она идёт от нуля до конца голосовой части и задаёт ритм перебивкам.
MUSIC_TRACK = "A3"
# Паузы эталона: 3.6 с после стендапа, 13 с между спикерами. В паузах
# нарезка без звука — вместо болтовни зала звучит музыка.
GAP_AFTER_STANDUP = 3.6
GAP_BETWEEN_SPEAKERS = 13.0
# Первый бролл заходит под конец стендапа на 2.2 с (J-cut): в эталоне
# вход на 21.48 при стендапе до 23.68. Перекрывать сам стендап нельзя.
J_CUT = 2.2
# Первые секунды спикера открыты — первую реплику не перекрывать,
# зритель должен увидеть, кто говорит. Перебивки со второй реплики.
OVR_OPEN = 8.0
# Порог «тот же сюжет». Фон сходства описаний нарезки: медиана 0.872,
# p95 0.929, p99 0.950. Ниже фона отсекается всё подряд.
SAME_SCENE = 0.95
# Меры вырезки нарезки: 1.2-2.6 с, замер готового дайджеста (101.7 с
# собран вырезками этих длин). Целые клипы (5-13 с) отменены: их всё
# равно режет монтажёр, а в хвосте они вылезали за потолок хронометража.
BROLL_MEASURES = (1.2, 1.55, 1.9, 2.25, 2.6)

# Сколько перебивок можно положить поверх интервью и в паузу между
# спикерами. Правка пользователя: «на перебивки интервьюшек мы
# используем всего 3 кадра, а не 8». В эталоне над интервью C0098
# ровно 3 перебивки (C0083, C0054, C0088) — столько и кладём.
IV_BROLL_MAX = 3
# В паузе между спикерами кусков четыре, а не три: пауза в 13 с тремя
# кусками по 4.3 с не закрывается (третий исходник кончается) — на
# таймлайне остаётся дыра в 1.8 с. Эталон в паузе кладёт пять кусков
# по 2.9-4.3 с, так что четыре — между «3 кадра» пользователя и
# эталоном.
GAP_BROLL_MAX = 4
# В паузе куски длиннее обычной меры: три куска по 2.6 с не закрывают
# паузу в 13 с, и на таймлайне остаётся дыра. Эталон в паузе кладёт
# куски по 2.9-4.3 с.
GAP_MAX_LEN = 4.5
# MONO_GAP живёт в блоке констант речи выше: best_monologue() берёт его
# значением по умолчанию.


def otbor_rows(rows, parts):
    """Не взятые в сборку исходники -> строки плана секвенции «Отбор».

    Монтажёр смотрит «Отбор», когда какой-то кадр в сборке не
    понравился: здесь лежит всё, чего в сборке нет, целиком, со звуком
    из самого файла (клип без колонки audio занимает собственную
    звуковую дорожку). Куски идут подряд по именам файлов, без
    наложений. Музыку в «использованные» не считаем: mp3 — не исходник.

    Исходники берём из папок частей (стендап/нарезка/интервью), а не из
    корня проекта: материал лежит уровнем ниже (материал/исх/...), а в
    корне валяются чужие файлы — прежний ролик и сам проект.
    """
    used = {Path(r[1]).name for r in rows if r[0] != "музыка"}
    if isinstance(parts, dict):
        folders = [parts.get(k) for k in ("standup", "broll", "interview")]
    elif isinstance(parts, (list, tuple)):
        folders = list(parts)
    else:
        folders = [parts]
    out = []
    pos = 0.0
    seen = set()
    for folder in folders:
        for f in videos(folder):
            if f.name in used or f.name in seen:
                continue
            seen.add(f.name)
            dur = clip_duration(f)
            if dur <= 0:
                # Нет длительности — в Premiere такой клип не поставить,
                # молча пропускать тоже плохо: пусть монтажёр увидит в
                # журнале, что файл остался за бортом.
                print("Отбор: не померена длина {}, в отбор не попал".format(f.name))
                continue
            out.append(("отбор", f, 0.0, dur, "просмотр: " + f.name, "V1",
                        pos, ""))
            pos += dur
    return out


def build_digest(standup, broll, interviews, labels, weights, log,
                 target, neural=True, rec=None, music=None):
    """Рыба дайджеста по эталонному проекту.

    **Два слоя.** В эталоне («дайджест домашка») голос лежит на V1, а
    нарезка — на V2 отдельным слоем со своим таймлайном. Нарезка
    видна там, где голоса нет, и лежит под голосом там, где он есть:
    монтажёр гасит V1 и смотрит, что под ним.

    **Что в нарезке.** Поверх голоса и в паузах — короткие вырезки
    по мере 1.2-2.6 с. Кусок обязан влезать в свой слот (до конца
    реплики или до конца паузы): перекрытий внутри дорожки нарезки
    быть не должно, в эталоне их нет.

    **Что в голосе.** Стендап — один непрерывный кусок (в эталоне
    23.7 с, разбит на шесть частей). Интервью — куски по 1-8 с, и
    паузы из интервью вырезаны: в эталоне из 185-218 с выбрано 23.4 с
    речи.

    Нарезка подбирается ПО ТЕМЕ: что сказано — что показано. Тема
    реплики сравнивается с описанием кадра (его сделала модель) и с
    тем, что говорит в СВОЕМ звуке сам клип. Это черновик, а не
    решение: монтажёр правит его в панели разбора.
    """
    rows = []            # (часть, файл, in, out, текст, дорожка, позиция, звук)
    used = set()
    _dur_cache = {}      # длина клипа нарезки по имени файла
    rec_index = (rec or {}).get("index") or {}
    rec_dir = (rec or {}).get("dir")
    rec_dir_path = Path(rec_dir) if rec_dir else None
    rec_sources = {}
    if rec_dir_path:
        try:
            for p in rec_dir_path.iterdir():
                if recsound.is_rec_file(p):
                    # Имя в индексе — stem файла; регистр на разных
                    # файловых системах может отличаться, поэтому ключ
                    # приводим к верхнему.
                    rec_sources[p.stem.upper()] = p
        except OSError as e:
            log("  Рекордер: папка не читается: {}".format(str(e)[:80]))
    rec_notes = set()
    rec_durations = {}

    def rec_note(msg):
        """Одна и та же причина не должна повторяться на каждый кусок."""
        if msg not in rec_notes:
            rec_notes.add(msg)
            log("  Рекордер: " + msg)

    # ---- проверка сдвига рекордера по звуку ----
    # Слова дают кандидатов и якорь сдвига, но у дублей текст один и
    # тот же: словесный матч уводил звук в чужой дубль (рассинхрон в
    # готовом ролике). Звук дубли различает: на верном сдвиге огибающая
    # камеры совпадает с огибающей рекордера, на чужом — нет.
    work_dir = (rec or {}).get("work")
    _env_cache = {}
    _sync_cache = {}

    def env_of(src):
        """Огибающая звука файла: (env, rate), кэш по пути.

        Через временный mono-16k WAV: оригинал не трогаем, а ffmpeg
        гоняем один раз на файл.
        """
        key = str(src)
        if key not in _env_cache:
            wav = Path(work_dir) / ("wav_" + hashlib.md5(
                key.encode("utf-8")).hexdigest()[:12] + ".wav")
            if not wav.exists():
                sync.to_mono16k(src, wav)
            env, rate = sync.envelope(sync.read_wav(wav))
            _env_cache[key] = (env, rate)
        return _env_cache[key]

    def wave_pair(path, rec_src, delta_w, c0):
        """Сдвиг рекордера по звуку против словесного якоря.

        `delta_w` — сдвиг от слов; окно рекордера ищем рядом с ним,
        иначе поиск по всему файлу ловит ложные пики на далёком крае
        (правило AGENTS: широкое окно рождает фантомные совпадения).
        Возвращает (delta, quality, contrast) или (None, 0.0, 0.0);
        delta такой, что время_рекордера = время_камеры + delta.
        """
        try:
            cenv, rate = env_of(path)
            renv, _rr = env_of(rec_src)
        except Exception as e:
            rec_note("звук не читается: {}".format(str(e)[:80]))
            return None, 0.0, 0.0
        cam_len = len(cenv) / rate
        rec_len = len(renv) / rate
        c0 = min(max(0.0, c0), max(0.0, cam_len - 8.0))
        r0 = c0 + float(delta_w or 0.0)
        r0 = min(max(0.0, r0), max(0.0, rec_len - 8.0))
        n = min(110.0, cam_len - c0, rec_len - r0)
        if n < 8.0:
            return None, 0.0, 0.0
        ci, ri, ni = int(c0 * rate), int(r0 * rate), int(n * rate)
        try:
            off, q, co = sync.find_offset(renv[ri:ri + ni], cenv[ci:ci + ni],
                                          rate=rate, max_lag=25.0)
        except Exception as e:
            rec_note("сверка звука не удалась: {}".format(str(e)[:80]))
            return None, 0.0, 0.0
        # find_offset вернул (0,0,0) — общего сигнала в окне нет
        if q == 0.0 and co == 0.0:
            return None, 0.0, 0.0
        # Слабый и не острый пик верить не может: правило доверия sync.
        if q < sync.TRUST_QUALITY and co < sync.TRUST_CONTRAST:
            return None, q, co
        # Окно референса начинается на r0, окна камеры — на c0, поэтому
        # найденный сдвиг off считается от этих начал: пересчёт в
        # сдвиг целых файлов — off + r0 - c0.
        return off + r0 - c0, q, co

    def sync_hit(path, words, c0=0.0):
        """Проверенный звуком сдвиг рекордера для файла камеры.

        Слова дают список кандидатов — у дублей текст один, и выбрать
        по словам нельзя. Каждый кандидат проверяется огибающей: свой
        дубль совпадает по звуку, чужой — нет. Пары считаем один раз
        (сдвиг на файле константен, замер эталона: один Δ на весь кусок).

        Возвращает (имя, сдвиг, качество) или (None, None, 0.0).
        """
        if not rec_index or not work_dir or not words:
            return None, None, 0.0
        best = None
        for name, off_w, q_words in recmatch.rank(words, rec_index):
            src = rec_sources.get(str(name).upper())
            if src is None:
                continue
            key = (Path(path).name, src.name)
            if key not in _sync_cache:
                hit = wave_pair(path, src, off_w, c0)
                _sync_cache[key] = hit
                if hit[0] is not None:
                    log("  Синхрон: {} <-> {} Δ={:.2f}с (звук q={:.2f}, "
                        "контраст {:.1f}, слова {:.2f})".format(
                            Path(path).name, src.name, hit[0],
                            hit[1], hit[2], q_words))
            else:
                hit = _sync_cache[key]
            if hit[0] is None:
                continue
            if best is None or (hit[1], hit[2]) > (best[2], best[3]):
                best = (name, hit[0], hit[1], hit[2])
        if best is None:
            return None, None, 0.0
        return best[0], best[1], best[2]

    def voice_audio(path, i, o, chunk, hit=None):
        """Звук с рекордера для голосового куска: словарь или пусто.

        Микрофон стоял на рекордере, и в эталонном проекте голос лежит
        на отдельной дорожке: картинка с камеры, звук с рекордера.

        Возвращаем ИСХОДНЫЙ файл и его собственные in/out, а не
        нарезанный WAV. Нарезанный начинается с нуля, и premiere.py
        подставлял бы к нему камерный таймкод — звук уезжал или не
        находился вовсе. Одна дорожка A2 на все куски: иначе каждый
        вырезанный файл создавал бы свою звуковую дорожку.

        `hit` — готовое совпадение (имя рекордера, сдвиг): для
        интервью оно одно на весь ответ спикера.
        """
        if not rec_index or not rec_dir_path:
            return ""
        if hit is None:
            if not chunk:
                return ""
            hit = recmatch.find(chunk, rec_index)
        name, off = hit[0], hit[1]
        if name is None or off is None:
            rec_note("для {} совпадения нет".format(Path(path).name))
            return ""
        src = rec_sources.get(str(name).upper())
        if src is None:
            rec_note("нет файла {} в {}".format(name, rec_dir_path))
            return ""
        start = i + off
        # До -0.25с допускаем, как recsound.cut: дальше звук уже
        # начинается раньше записи, и подкладывать его нельзя.
        if start < -0.25:
            rec_note("{} начинается раньше записи на {:.2f}с".format(
                src.name, -start))
            return ""
        if start < 0:
            start = 0.0
        key = str(src)
        duration = rec_durations.get(key)
        if duration is None:
            duration = recsound.probe_duration(src)
            rec_durations[key] = duration
        return {"path": str(src), "in": start, "out": start + (o - i),
                "duration": duration, "track": "A2"}
    st_budget = target * SHARE_STANDUP
    nar_budget = target * SHARE_BROLL
    iv_budget = target - st_budget - nar_budget

    # Кадровая сетка. Таймлайн Premiere считается целыми кадрами, и
    # позиции плана обязаны на ней лежать: 23.22 с * 25 = 580.5 — ровно
    # полкадра, конец куска округлялся вверх, а начало следующего вниз,
    # и сосед налезал на соседа на кадр (в живом прогоне FX20028
    # налезал на C0015). Квантование снимает развязки на уровне плана.
    fps = int(cfg("project.fps", 25))

    def qf(sec):
        """Секунды -> ближайший кадр (на сетку)."""
        return round(sec * fps) / fps

    # Меры нарезки в кадрах: 1.55*25 = 38.75 нельзя записать в таймлайн,
    # поэтому мера тоже квантуется (ближайший целый кадр).
    mq = tuple(max(1, int(round(m * fps))) for m in BROLL_MEASURES)

    # Векторы подписей клипов: ими меряется и смысл, и повторяемость.
    # Считаются один раз: на каждый подбор заново считать дорого, а
    # клипов под сотню.
    vecs = None
    if neural and broll:
        try:
            vecs = theme.embed_labels(labels)
        except Exception as e:
            log("  Векторы не посчитались ({}), подбор по теме — по словам"
                .format(str(e)[:60]))
            vecs = None
    recent = []
    history = []
    last_num = [None]
    used_themes = set()
    nar_spent = [0.0]

    def varied(seg):
        """Не больше двух подряд одной пары (крупность, кто в кадре).

        Замер на готовом ролике: редактор чередовал общий, средний и
        крупный, и три одинаковых подряд не шло ни разу. Ряд из трёх
        похожих кадров монтажёр видит как «монтаж не сделан».
        """
        _p, _a, _b, _t, plan, hero = seg
        if not plan and not hero:
            return True
        same = [h for h in history[-2:] if h == (plan, hero)]
        return len(same) < 2

    def near_number(name):
        """Не идти подряд по соседним номерам файлов.

        Нумерация камеры идёт по порядку съёмки: C0011, C0012, C0013 —
        это один и тот же объект, и три раунда подряд не показывают.
        """
        if last_num[0] is None:
            return False
        return abs(clip_number(name) - last_num[0]) <= 3

    def too_similar(name):
        """Не повторять один и тот же сюжет подряд.

        Порог — не круглое число, а измеренный фон: описания ВСЕХ
        клипов нарезки похожи, потому что снято одно мероприятие.
        Замер по 110 описаниям: медиана 0.872, p90 0.918, p95 0.929,
        p99 0.950. Ставь 0.86 — и отсекается всё, подбор пустеет.
        Поэтому порог выше фона: тот же сюжет — это верхний процент.
        """
        if vecs is None or name not in vecs:
            return False
        for other in recent[-2:]:
            if other and other in vecs and float(vecs[name] @ vecs[other]) > SAME_SCENE:
                return True
        return False

    def theme_key(seg):
        """Тема клипа по его собственному звуку.

        Клипы без речи темы не имеют, и ограничивать их нечем: ключ
        пустой, и проверка их пропускает.
        """
        lab = labels.get(seg[0].name) or {}
        return " ".join((lab.get("text") or "").split()).lower()

    def seg_dur(path):
        """Длительность клипа нарезки: нужна, чтобы дорастить вырезку.

        Кэш по имени файла: один клип берётся в монтаж один раз, но
        длина его может понадобиться и при отборе, и при раскладке.
        """
        key = path.name
        if key not in _dur_cache:
            try:
                _dur_cache[key] = float(clip_duration(path) or 0.0)
            except Exception:
                _dur_cache[key] = 0.0
        return _dur_cache[key]

    def take_broll(seg, pos, limit=None):
        path, a, b, text, plan, hero = seg
        # Кусок обязан влезать в свой слот: иначе он налезает на
        # следующий кусок нарезки, а перекрытий внутри дорожки в
        # эталоне нет. Длина окна — целое число кадров: соседние
        # куски сцепляются в XML без полукадровых развязок.
        eff = (b - a) if limit is None else min(limit, b - a)
        if limit is not None and limit > eff + 1e-6:
            # Кусок можно дорастить до предела слота: клип нарезки
            # длиннее своей вырезки, а предел слота (пауза между
            # спикерами) длиннее вырезки. Без этого в паузе остаётся
            # дыра — она читается как недоделанный монтаж, в эталоне
            # пауза закрыта кусками по 2.9-4.3 с.
            dur = seg_dur(path)
            if dur > 0 and a + limit <= dur + 0.02:
                eff = limit
        b = a + max(1, int(eff * fps + 1e-6)) / fps
        used.add(path.name)
        recent.append(path.name)
        history.append((plan, hero))
        last_num[0] = clip_number(path.name)
        key = theme_key(seg)
        if key:
            used_themes.add(key)
        nar_spent[0] += b - a
        # "нет" в конце — перебивка выкладывается без звукового клипа:
        # под ней играет рекордер (поверх речи) или музыка (паузы).
        return ("нарезка", path, a, b, text[:140], BROLL_TRACK, pos, "нет")

    def pick_for(text, limit):
        """Клипы под тему текста: по описанию кадра и по его звуку."""
        got = []
        if not text:
            return got
        best = theme.best_matches(text, labels, weights,
                                  exclude=used, limit=limit * 4,
                                  neural=neural, vecs=vecs)
        why = []
        for name, _score in best:
            if name in used or too_similar(name):
                why.append(name + ": уже взят или похож")
                continue
            seg = broll_by_name(broll, name)
            if seg is None:
                why.append(name + ": нет в списке")
                continue
            if not varied(seg) or near_number(name):
                why.append(name + ": план/номер соседний")
                continue
            key = theme_key(seg)
            if key and key in used_themes:
                # Одна тема — один кадр. Иначе в ролике два раза
                # показывают один и тот же кусок мероприятия, и монтажёр
                # видит это как «монтаж не сделан».
                continue
            got.append(seg)
            if len(got) >= limit:
                break
        if not got and why:
            log("  подбор по теме ничего не дал: " + "; ".join(why[:3]))
        return got

    # ---- слой 1: голос ----
    # Стендап — один непрерывный кусок, интервью — куски по фразам с
    # паузами между спикерами. Всё это на одной дорожке: голос.
    t = 0.0
    voice = []           # (начало, конец, текст реплики)
    spoken = []          # весь голос, включая стендап
    slots = []           # слоты нарезки: (начало, конец, тема, добор-хвостом)
    standup_text = ""
    for path, i, o, text, chunk in standup:
        if (o - i) > st_budget and rows:
            break
        d = qf(o - i)
        hit = sync_hit(path, chunk, i)
        rows.append(("стендап", path, i, i + d, text[:200], VOICE_TRACK, t,
                     voice_audio(path, i, i + d, chunk, hit)))
        spoken.append((t, t + d, text))
        standup_text += " " + text
        t += d
    standup_end = t
    t += qf(GAP_AFTER_STANDUP)
    prev_end = None      # конец голоса предыдущего ВЗЯТОГО спикера
    for spk_no, (path, pieces, words) in enumerate(interviews):
        # Звук с рекордера ищем ОДИН РАЗ на спикера, а не на каждый
        # кусок: короткий кусок в два-три слова рекордеру не
        # соответствует, и звук терялся у половины кусков. Сопоставляем
        # по ВСЕМ словам ответа, а не по началу файла: в рекордере
        # вопрос ведущего сформулирован иначе и якорь по нему не
        # срабатывает. Слова берём целиком (монолог — это только
        # отобранный кусок), иначе совпадение может не найтись.
        ans_words = words
        rec_hit = sync_hit(path, ans_words, pieces[0][0] if pieces else 0.0)
        if rec_index and rec_hit[0] is None:
            # Спикер без звуковой пары с рекордером не берётся: без
            # сдвига куски играют чужим или камерным звуком — тот самый
            # «набор каких-то звуков» из фидбэка. В эталоне у такого
            # соседа-дубля записи в рекордере просто нет. Снятый спикер
            # и паузы не добавляет: между соседними ВЗЯТЫМИ спикерами
            # ровно один промежуток, иначе сборка раздувается вдвое.
            log("  Интервью: {} не сведён с рекордером — не берём"
                .format(Path(path).name))
            continue
        if prev_end is not None:
            t = prev_end + qf(GAP_BETWEEN_SPEAKERS)
        left = iv_budget
        spk_t0 = t
        spans = []          # куски спикера на таймлайне
        for i, o, text, chunk in pieces:
            d = qf(o - i)
            if left <= 0 or (t + d) > target:
                break
            rows.append(("интервью", path, i, i + d, text[:200], VOICE_TRACK, t,
                         voice_audio(path, i, i + d, chunk, rec_hit)))
            voice.append((t, t + d, text))
            spoken.append((t, t + d, text))
            spans.append((t, t + d, text))
            t += d
            left -= d
        if spans:
            prev_end = t
        # Слоты перебивок поверх реплик. Первые OVR_OPEN секунд спикера
        # открыты: зритель должен увидеть, кто говорит, — первую реплику
        # не перекрываем (в эталоне спикер открыт ~8 с).
        for pt0, pt1, text in spans:
            s0 = max(pt0, spk_t0 + OVR_OPEN)
            if pt1 - s0 >= 1.0:
                # Поверх одного интервью — не больше IV_BROLL_MAX
                # перебивок: иначе голос участника исчезает под нарезкой
                # (жалоба пользователя на прошлую сборку).
                slots.append({"s": s0, "e": pt1, "theme": text,
                              "leftover": False, "cap": IV_BROLL_MAX,
                              "group": "iv{}".format(spk_no),
                              "maxlen": None})
    voice_end = t

    if music is not None and voice_end > 0.5:
        # Музыкальная подложка эталона: от нуля до конца голосовой
        # части, одной строкой, на своей дорожке A3. Она же задаёт
        # ритм, по которому ставятся перебивки.
        rows.append(("музыка", music, 0.0, voice_end, "музыкальная подложка",
                     MUSIC_TRACK, 0.0, str(music)))

    # ---- слой 2: нарезка ----
    # Подбор ТОЛЬКО по теме. Никакого «просто остальные клипы подряд»:
    # в эталоне такого нет, а монтажёр такое видит как «монтаж не сделан».
    #
    # Перебивки идут БЕЗ СВОЕГО ЗВУКА (sound = "нет"): поверх речи
    # звучит рекордер, в паузах и хвосте — музыка. Со своим звуком из
    # видеофайла они заглушали интервью — жалоба на прошлую сборку.

    # J-cut: первый бролл заходит под конец стендапа (в эталоне вход
    # за 2.2 с при стендапе до 23.68). Один кусок, без звука: кадр уже
    # идёт, когда голос стендапа ещё звучит.
    if standup and nar_spent[0] < nar_budget and standup_end > J_CUT + 0.5:
        for seg in pick_for(standup_text.strip(), 1):
            rows.append(take_broll(seg, standup_end - J_CUT, limit=J_CUT))
            break

    # Паузы между блоками голоса — продолжение темы: берём нарезку по
    # словам последней сказанной реплики. Меры те же, пауза ограничивает
    # кусок сверху. В эталоне паузы заполнены всегда: чёрная дыра на
    # V1/V2 выдавала бы недоделанный монтаж.
    prev = 0.0
    last_text = standup_text.strip()
    gap_no = 0
    for start, end, text in sorted(spoken):
        if start - prev > 0.5:
            # Пауза между спикерами — это тоже «перебивки интервьюшек»:
            # в прошлой сборке их шло 8 подряд, и интервью в таймлайне
            # пропадало. Кладём GAP_BROLL_MAX кусков, зато длиннее:
            # пауза закрывается целиком, без дыр.
            slots.append({"s": prev, "e": start, "theme": last_text,
                          "leftover": False, "cap": GAP_BROLL_MAX,
                          "group": "gap{}".format(gap_no),
                          "maxlen": GAP_MAX_LEN})
            gap_no += 1
        prev = end
        last_text = text

    # Хвост после голоса — вырезки той же меры 1.2-2.6 с, что и
    # остальная нарезка. Целые клипы отменены: реальные куски выходили
    # по 7.7-9.6 с, а их всё равно режет монтажёр. Тема хвоста —
    # последние сказанные слова: они и задают, что мы показываем в
    # конце. Подряд «просто оставшиеся клипы» не берём никогда:
    # монтажёр такое видит как «монтаж не сделан».
    def leftover_cand():
        """Дозаполнение хвоста, когда по теме последних слов подходящих
        не осталось. Хвост по бюджету длиннее одной темы: раньше это
        сходилось, потому что каждый кандидат сжигал 9 с клипа, теперь
        по 2.6 с максимум — кандидаты по одной теме кончаются за пару
        тысяч кадров. Остальные берём по тем же правилам отбора: не тот
        же сюжет, не соседние номера, не два одинаковых плана подряд.
        """
        for seg in broll:
            name = seg[0].name
            if name in used or too_similar(name):
                continue
            if not varied(seg) or near_number(name):
                continue
            key = theme_key(seg)
            if key and key in used_themes:
                continue
            return seg
        return None

    tail_theme = (voice[-1][2] if voice else standup_text) or standup_text
    if target - voice_end >= 1.0:
        slots.append({"s": voice_end, "e": target, "theme": tail_theme,
                      "leftover": True, "cap": None, "group": None,
                      "maxlen": None})

    # Раскладка слотов по таймлайну: сначала ранние (J-cut уже стоит),
    # паузы и хвост получают бюджет раньше далёких оверлеев.
    slots.sort(key=lambda s: s["s"])
    measure_i = 0
    counts = {}            # сколько перебивок уже лежит в группе
    for slot in slots:
        start, end = slot["s"], slot["e"]
        theme_text = slot["theme"]
        allow_leftover = slot["leftover"]
        cap = slot.get("cap")
        grp = slot.get("group")
        maxlen = slot.get("maxlen")
        # Имя не `used`: выше `used` — множество уже взятых клипов, и
        # перебивка его затирала (пикер получал число вместо множества).
        used_in_grp = counts.get(grp, 0) if grp else 0
        left = (cap - used_in_grp) if (cap and grp) else None
        pos = start
        while pos < end - 0.5 and nar_spent[0] < nar_budget:
            if left is not None and left <= 0:
                break
            seg = None
            for cand in pick_for(theme_text, 1):
                seg = cand
                break
            if seg is None and allow_leftover:
                seg = leftover_cand()
            if seg is None:
                break
            room = max(1, int((end - pos) * fps + 1e-6)) / fps
            if maxlen:
                # В паузе кусок длиннее обычной меры: иначе лимит в
                # 3 кадра оставит в паузе дыру.
                share = room / left if left else room
                limit = min(maxlen, room, share)
            else:
                limit = min(mq[measure_i % len(mq)], room)
            measure_i += 1
            if limit < 1.0:
                break
            row = take_broll(seg, pos, limit=limit)
            rows.append(row)
            pos += row[3] - row[2]
            if left is not None:
                left -= 1
                used_in_grp += 1
                counts[grp] = used_in_grp
    return rows


def clip_number(name):
    """Номер клипа по имени: C0028 -> 28, FX20020 -> 20020.

    Нумерация камеры идёт по порядку съёмки, поэтому соседние номера —
    это один и тот же объект. На этом строится правило «не идти подряд
    по соседним файлам».

    Расширение отбрасывается: иначе в номер попадают цифры из `.MP4`
    и C0028.MP4 превращался в 284.
    """
    stem = name.rsplit(".", 1)[0] if "." in name else name
    digits = "".join(ch for ch in stem if ch.isdigit())
    return int(digits) if digits else 0


def write_shelf(broll, labels, plans, rows, out_csv: Path):
    """Полка для монтажёра: все клипы нарезки с подписями.

    Человек тратит на отсмотр материала 2-3 часа. Открывать 110 файлов
    подряд — это и есть эти часы. Здесь всё в одной таблице: что в
    клипе, кто в кадре, сколько длится, куда попал в рыбу.

    `rows` — собранная рыба, чтобы отметить, что уже использовано.
    """
    used = {}
    for row in rows:
        part, path, a, b = row[0], row[1], row[2], row[3]
        if part == "нарезка":
            used[path.name] = "{:.1f}-{:.1f}".format(a, b)
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["файл", "в рыбе", "in", "out", "крупность", "кто в кадре",
                    "тема (из звука клипа)", "что в кадре (описание)",
                    "длительность"])
        for path, a, b, text, plan, hero in broll:
            lab = labels.get(path.name) or {}
            dur = float(lab.get("dur") or 0.0)
            desc = (lab.get("desc") or "").strip()
            w.writerow([path.name, used.get(path.name, ""),
                        "{:.1f}".format(a), "{:.1f}".format(b),
                        plan, hero, text or "(без речи)",
                        desc or "(не описано)",
                        "{:.1f}".format(dur)])
    return out_csv


def read_plans(broll_csv: Path):
    """Что detect.py насчитал: {файл: (крупность, кто в кадре)}.

    Пустой set означает «отсева не было, берём всё»: так честнее, чем
    молча выкинуть нарезку, если разбор не удался.
    """
    plans = {}
    try:
        with broll_csv.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                name = (r.get("file") or "").strip()
                if name:
                    plans[name] = ((r.get("plan") or "").strip(),
                                   (r.get("hero") or "").strip())
    except Exception:
        return set(), {}
    return set(plans.keys()), plans


def broll_by_name(broll, name):
    for seg in broll:
        if seg[0].name == name:
            return seg
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Собрать дайджест: стендап, нарезка, интервью")
    ap.add_argument("--input", required=True, help="папка с тремя частями")
    ap.add_argument("--out", required=True, help="куда положить plan.csv")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--target-sec", type=float, default=None,
                    help="хронометраж дайджеста; пусто — digest.target_sec (120)")
    args = ap.parse_args(argv)

    # Абсолютный base: иначе рекордер, найденный относительно него,
    # вернёт относительный путь, и write_plan приклеит base второй раз.
    base = Path(args.input).expanduser().resolve()
    if not base.is_dir():
        print("Нет папки: " + str(base))
        return 1
    # base — то, что указал пользователь и что уйдёт в Premiere как
    # media-dir. Фактическая папка с частями может быть вложенной (исх),
    # поэтому ищем её отдельно, а пути в плане считаем от base.
    root, hint = sorter.find_materials(base)
    if root is None:
        if hint:
            print(hint)
        else:
            print("В папке не нашлось материала: " + str(base))
        return 1
    if hint:
        print("{}: {}".format(hint, root))
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_work"
    work.mkdir(parents=True, exist_ok=True)

    def log(s):
        print(s)

    parts = resolve_parts(root)
    missing = [n for k, v in parts.items() if v is None
               for n in [sorter.part_names()[k]]]
    if missing:
        print("Нет папок частей: {}. Сначала разберите материал (sorter.py)."
              .format(", ".join(missing)))
        return 1

    target = args.target_sec
    if target is None:
        target = float(settings.get("digest.target_sec", 120) or 120)

    # ---- что в кадрах нарезки: описание моделью ----
    # Без этого нарезка выбирается «первыми по алфавиту», и ролик
    # выглядит как случайный набор кадров.
    desc_model = str(settings.get("digest.describe_model",
                                     describe.DEFAULT_MODEL) or "")
    descs = {}
    if desc_model:
        log("Нарезка: описываю кадры ({})".format(desc_model.split("/")[-1]))
        try:
            descs = describe.describe_pool(parts["broll"], work, desc_model,
                                           args.device, log)
        except Exception as e:
            log("  Описать кадры не вышло ({}), подбор будет по звуку"
                .format(str(e)[:80]))
            descs = {}
    log("Нарезка: описано {} клипов".format(len(descs)))

    # ---- темы нарезки: из её собственного звука ----
    log("Нарезка: подписываю клипы по их звуку")
    labels = theme.label_pool(parts["broll"], work, args.model, args.device,
                               log, descs=descs)
    for name in labels:
        labels[name]["dur"] = clip_duration(parts["broll"] / name)
    weights = theme.idf(labels)
    voiced = len([n for n in labels if theme.content_words(
        theme.label_text(labels[n]))])
    log("Нарезка: подписано {} клипов, из них с описанием или речью {}"
        .format(len(labels), voiced))

    # ---- стендап: один лучший дубль, нарезанный по фразам ----
    st_budget = target * SHARE_STANDUP
    # Файлы рекордера: рядом с частями, в папке звк. Если их нет,
    # голос останется со звуком камеры — это хуже, но не хуже, чем
    # остановиться.
    rec = {"index": {}, "dir": None, "work": work}
    rec_dir = recsound.find_rec_dir(root)
    if rec_dir:
        log("Рекордер: {}".format(rec_dir))
        rec["dir"] = rec_dir
        rec["index"] = recsound.rec_index(rec_dir, work, args.model, log=log)
        log("Рекордер: файлов с речью {}".format(len(rec["index"])))
    front = process_standup(parts["standup"], work, args.model, args.device, log,
                            budget=st_budget, rec_index=rec["index"])

    # ---- нарезка: короткие вырезки из отобранных клипов ----
    # autocut здесь не участвует: он подбирает клипы под БЮДЖЕТ хронометража,
    # а в дайджесте нужны короткие вырезки из всех годных клипов. Нарезка
    # в готовом ролике — это 33 куска по 1.2-2.6 с из 28 разных файлов, а
    # не два клипа по 15 с.
    b_path = work / "broll.csv"
    broll = []
    keep, plans = read_plans(b_path)
    if broll_csv(parts["broll"], b_path, log, args.device):
        keep, plans = read_plans(b_path)
        broll = broll_segments(parts["broll"], labels, log, keep=keep,
                               plans=plans)
    log("Нарезка: вырезок {}".format(len(broll)))

    # ---- интервью: куски по фразам ----
    iv_budget = target - target * SHARE_STANDUP - target * SHARE_BROLL
    back = process_interviews(parts["interview"], work, args.model, args.device,
                              log, budget=iv_budget)

    # Распознавание закончено. Whisper занимает 3 ГБ, кодировщик темы
    # ещё 2.2 ГБ, а видеопамяти 6 ГБ. Освобождаем здесь, а не раньше:
    # rec_index и нарезка ещё пользуются моделью.
    speech.release()

    # Музыкальная подложка: в эталоне mp3 лежит рядом с материалом и
    # идёт от нуля до конца голосовой части. Сначала пробуем настройку
    # digest.music, затем любой mp3 в корне папки материала.
    music_name = str(cfg("music", "") or "")
    music = None
    if music_name:
        mp = Path(music_name)
        if not mp.is_absolute():
            mp = base / mp
        if mp.exists():
            music = mp
        else:
            log("Музыка: {} не найдена".format(music_name))
    if music is None:
        try:
            mp3s = sorted(base.glob("*.mp3"))
        except OSError:
            mp3s = []
        if mp3s:
            music = mp3s[0]
    if music is not None:
        log("Музыка: {}".format(music.name))
    else:
        log("Музыка: mp3 в корне материала нет — идём без подложки")

    rows = build_digest(front, broll, back, labels, weights, log, target,
                        rec=rec, music=music)

    # Музыка на стендапе и интервью уходит вниз — она фон, а не второй
    # голос. Приглушаем сам файл (копию в work), исходник не трогаем.
    if music is not None:
        ducked = duck_music(music, rows, work, log)
        if ducked is not None:
            rows = [r[:7] + (str(ducked),) if r[0] == "музыка" else r
                    for r in rows]
            log("Музыка: приглушена на стендапе и интервью ({})"
                .format(ducked.name))

    if not rows:
        print("Собрать нечего: все три части пустые.")
        return 1

    plan = write_plan(rows, out_dir / "plan.csv", base)
    # Отбор — то, чего в сборке нет: монтажёр смотрит эти исходники,
    # когда какой-то кадр в сборке не понравился.
    ot = otbor_rows(rows, parts)
    otbor = write_plan(ot, out_dir / "otbor.csv", base)
    shelf = write_shelf(broll, labels, plans, rows, out_dir / "shelf.csv")
    # Хронометраж — конец таймлайна (позиция + длительность последнего
    # куска), а не сумма кусков: нарезка лежит двумя слоями поверх
    # голоса, и сумма кусков больше ролика. В живом прогоне сумма дала
    # 117.6 с, а конец таймлайна — 97.9 с.
    span = max(r[6] + (r[3] - r[2]) for r in rows)
    log("\nПлан: " + str(plan))
    log("Полка для монтажёра: " + str(shelf))
    if ot:
        log("Отбор: {} (не взятых в сборку исходников {})".format(otbor, len(ot)))
    else:
        log("Отбор: не взятых исходников нет — " + str(otbor))
    log("Кусов: {} (стендап {}, нарезка {}, интервью {}, музыка {})".format(
        len(rows),
        len([r for r in rows if r[0] == "стендап"]),
        len([r for r in rows if r[0] == "нарезка"]),
        len([r for r in rows if r[0] == "интервью"]),
        len([r for r in rows if r[0] == "музыка"])))
    log("Хронометраж: {:.1f}с (потолок {:.0f}с)".format(span, target))
    if span > target:
        log("  ПРЕВЫШЕНИЕ на {:.1f}с".format(span - target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
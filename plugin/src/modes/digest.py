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

Синхронизация не нужна нигде: части независимы, каждая приносит свой
звук в своей дорожке. Это отличает дайджест от интервью с двумя
камерами, где микрофоны стартуют в разное время.

Порядок в секвенции: стендап -> нарезка -> интервью. Сначала ведущий
объясняет, что сейчас будет, потом показываем, потом даём голос
участникам.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
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
from ml import theme        # noqa: E402


DIGEST_PREFIX = "digest"


def cfg(key, default):
    v = settings.get(DIGEST_PREFIX + "." + key, default)
    return v if isinstance(v, (int, float, str)) else default


# ---------------------------------------------------------------- части


def resolve_parts(root: Path):
    """Папки трёх частей.

    Человек указывает папку проекта, а материалы часто лежат во вложенной
    (`исх`). Сначала ищем части прямо в корне, если их нет — пробуем
    один уровень вглубь по тому же правилу, что и сортировщик. Иначе
    блок падал бы с «нет папок частей» при материале, который рядом.
    """
    names = sorter.part_names()
    for base in (root,) + tuple(sorted(
            [p for p in root.iterdir() if p.is_dir()] if root.is_dir() else [],
            key=lambda p: p.name)):
        got = {k: (base / names[k]) for k in ("standup", "broll", "interview")
               if (base / names[k]).is_dir()}
        if len(got) >= 2:
            got.setdefault("standup", None)
            got.setdefault("broll", None)
            got.setdefault("interview", None)
            return {"standup": got.get("standup"), "broll": got.get("broll"),
                    "interview": got.get("interview")}
    return {k: None for k in ("standup", "broll", "interview")}


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
        out.append((a, b, txt))

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


def pick_best_take(candidates):
    """Из дублей стендапа выбрать лучший.

    candidates: [(файл, слова)].
    """
    best = None
    for name, words in candidates:
        if not words:
            continue
        longest = longest_run(words)
        total = sum(t1 - t0 for _w, t0, t1 in words)
        pieces = phrase_pieces(words)
        worst = min(interview.chunk_score(i, o, t) for i, o, t in pieces) \
            if pieces else 0
        rank = (longest, total, -worst)
        if best is None or rank > best[0]:
            best = (rank, name, words, total)
    return best


def process_standup(folder: Path, work: Path, model_name, device, log,
                    budget=None):
    """Лучший дубль стендапа -> [(файл, in, out, текст)].

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
    best = pick_best_take(cands)
    if not best:
        log("Стенап: ни один дубль не дал разборчивой речи")
        return []
    _rank, path, words, total = best

    run = best_run(words, target)
    if run is None:
        log("Стенап: речь не разбилась на связные заходы")
        return []
    i, o, text = run
    log("Стенап: {} из {} дублей, речи {:.0f}с, берём один кусок {:.1f}с"
        .format(path.name, len(files), total, o - i))
    return [(path, i, o, text)]


def best_run(words, budget):
    """Самый длинный связный заход речи, влезающий в бюджет.

    Возвращает (начало, конец, текст) или None. Границы — по словам,
    чтобы не разрезать фразу.
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
    return (i, o, text)


def process_interviews(folder: Path, work: Path, model_name, device, log,
                       budget=None):
    """Ответы спикеров, нарезанные по фразам.

    Возвращает [(файл, [(in, out, текст), ...])] — по спикеру один
    список кусков. Редактор режет интервью по границам фраз: кусок
    «На решение отводится час» (1.16 с) стоит отдельно и не
    обрезается посередине. Поэтому кусков много и они короткие.
    """
    files = videos(folder)
    if not files:
        return []
    per = float(cfg("interview_answer_sec", 15.0))
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
        pieces = phrase_pieces(words, limit_sec=per)
        if not pieces:
            continue
        log("  {}: {} кусков по фразам, {:.0f}с".format(
            p.name, len(pieces), sum(o - i for i, o, _t in pieces)))
        out.append((p, pieces))
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
    """rows: [(часть, файл, in, out, текст/описание)] -> CSV для Premiere.

    Колонка `path` — путь файла ОТНОСИТЕЛЬНО `base`, того каталога,
    который пользователь указал в окне и который потом уходит в Premiere
    как media-dir. Если считать путь от папки с частями, получится
    «стендап/файл.mp4», и Premiere будет искать его прямо в корне
    проекта, где его нет: материал лежит на уровень глубже, в `исх`.
    Тогда Premiere просит relink на каждом клипе.
    """
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["n", "file", "part", "path", "in", "out", "duration",
                    "desc"])
        for i, (part, path, src_in, src_out, desc) in enumerate(rows, 1):
            try:
                rel = path.resolve().relative_to(base.resolve()).as_posix()
            except Exception:
                rel = path.name
            w.writerow([i, path.name, part, rel,
                        "{:.2f}".format(src_in), "{:.2f}".format(src_out),
                        "{:.2f}".format(src_out - src_in), desc])
    return out_csv


# Доли экранного времени. Замер на готовом дайджесте по совпавшим
# кадрам: нарезка 55.4 с из 101.7 (55%), интервью 24.6 с (24%),
# стендап 21.7 с (21%). Нарезка идёт первой по замыслу: «сначала
# ведущий объясняет, потом показываем, потом даём голос участникам» —
# и в готовом ролике показывают больше половины времени.
SHARE_STANDUP = 0.21
SHARE_BROLL = 0.55


def build_digest(standup, broll, interviews, labels, weights, log,
                 target, neural=True):
    """Рыба дайджеста по рецепту редактора.

    standup: [(файл, in, out, текст)] — куски лучшего дубля.
    broll: [(файл, in, out, подпись, план, кто в кадре)].
    interviews: [(файл, [(in, out, текст)])] — куски по фразам.

    Устройство — как в готовом ролике: стендап идёт спереди и звучит
    непрерывно, потом нарезка-подводка, потом интервью, а нарезка
    перемежает интервью блоками по 2–6 кусков.

    Нарезка подбирается ПО ТЕМЕ: что сказано — что показано. Тема
    реплики сравнивается с описанием кадра (его сделала модель) и с
    тем, что говорит в СВОЕМ звуке сам клип. Это черновик, а не
    решение: монтажёр правит его в панели разбора.
    """
    rows = []
    used = set()
    st_budget = target * SHARE_STANDUP
    nar_budget = target * SHARE_BROLL
    iv_budget = target - st_budget - nar_budget

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

        Два кадра подряд из одного места (дети за столом, дети за столом)
        монтажёр видит как «монтаж не сделан», даже если крупность
        разная.
        """
        if vecs is None or name not in vecs:
            return False
        for other in recent[-2:]:
            if other and other in vecs and float(vecs[name] @ vecs[other]) > 0.86:
                return True
        return False

    nar_spent = [0.0]

    used_themes = set()

    def theme_key(seg):
        """Тема клипа по его собственному звуку.

        Клипы без речи темы не имеют, и ограничивать их нечем: ключ
        пустой, и проверка их пропускает.
        """
        lab = labels.get(seg[0].name) or {}
        return " ".join((lab.get("text") or "").split()).lower()

    def take_broll(seg):
        path, a, b, text, plan, hero = seg
        used.add(path.name)
        recent.append(path.name)
        history.append((plan, hero))
        last_num[0] = clip_number(path.name)
        key = theme_key(seg)
        if key:
            used_themes.add(key)
        nar_spent[0] += b - a
        return ("нарезка", path, a, b, text[:140])

    def pick_for(text, limit):
        """Клипы под тему текста: по описанию кадра и по его звуку."""
        got = []
        if not text:
            return got
        best = theme.best_matches(text, labels, weights,
                                  exclude=used, limit=limit * 4,
                                  neural=neural, vecs=vecs)
        for name, _score in best:
            if name in used or too_similar(name):
                continue
            seg = broll_by_name(broll, name)
            if seg is None:
                continue
            if not varied(seg) or near_number(name):
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
        return got

    def block(text, limit):
        """Блок нарезки: куски под тему, пока хватает доли."""
        got = []
        for seg in pick_for(text, limit):
            if nar_spent[0] >= nar_budget:
                break
            got.append(take_broll(seg))
        return got

    # ---- стендап: голос в кадр, спереди, непрерывно ----
    standup_text = ""
    for path, i, o, text in standup:
        if (o - i) > st_budget and rows:
            break
        rows.append(("стендап", path, i, o, text[:200]))
        standup_text += " " + text

    # ---- нарезка-подводка: показываем, о чём речь в стендапе ----
    # В готовом ролике подводка — 6 кусков по 1.2–2.1 с, и это треть
    # всей нарезки.
    for seg in block(standup_text, 6):
        rows.append(seg)

    # ---- интервью, перемежаемое нарезкой ----
    # Редактор так и делал: ответ спикера (1–2 куска), потом блок
    # нарезки (2–6 кусков), потом следующий ответ.
    per = iv_budget / max(1, len(interviews))
    for n, (path, pieces) in enumerate(interviews):
        left = per
        first = True
        for i, o, text in pieces:
            if left <= 0:
                break
            rows.append(("интервью", path, i, o, text[:200]))
            left -= (o - i)
            if not first or n:
                # Блок нарезки под ответ: тема берётся из того, что
                # сказал спикер, клип — по совпадению темы с описанием
                # кадра.
                for seg in block(text, 4):
                    rows.append(seg)
            first = False

    # ---- закрывающий блок нарезки: до потолка хронометража ----
    # В готовом ролике последние пятнадцать секунд — сплошная нарезка.
    if nar_spent[0] < nar_budget:
        for seg in broll:
            if nar_spent[0] >= nar_budget:
                break
            if seg[0].name in used or too_similar(seg[0].name):
                continue
            if not varied(seg) or near_number(seg[0].name):
                continue
            key = theme_key(seg)
            if key and key in used_themes:
                continue
            rows.append(take_broll(seg))
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
    for part, path, a, b, _desc in rows:
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

    base = Path(args.input).expanduser()
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

    # Whisper освобождаем: распознавание закончено, а кодировщик темы
    # и так занимает 2.2 ГБ. Вместе они в 6 ГБ не влезают.
    speech.release()

    # ---- стендап: один лучший дубль, нарезанный по фразам ----
    st_budget = target * SHARE_STANDUP
    front = process_standup(parts["standup"], work, args.model, args.device, log,
                            budget=st_budget)

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

    rows = build_digest(front, broll, back, labels, weights, log, target)

    if not rows:
        print("Собрать нечего: все три части пустые.")
        return 1

    plan = write_plan(rows, out_dir / "plan.csv", base)
    shelf = write_shelf(broll, labels, plans, rows, out_dir / "shelf.csv")
    total = sum(o - i for _p, _f, i, o, _d in rows)
    log("\nПлан: " + str(plan))
    log("Полка для монтажёра: " + str(shelf))
    log("Кусов: {} (стендап {}, нарезка {}, интервью {})".format(
        len(rows),
        len([r for r in rows if r[0] == "стендап"]),
        len([r for r in rows if r[0] == "нарезка"]),
        len([r for r in rows if r[0] == "интервью"])))
    log("Хронометраж: {:.1f}с (потолок {:.0f}с)".format(total, target))
    if total > target:
        log("  ПРЕВЫШЕНИЕ на {:.1f}с".format(total - target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
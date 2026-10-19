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
from ml import sorter       # noqa: E402


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


# ---- модель и кеш распознавания ----
_MODELS = {}


def get_model(model_name: str, device: str):
    """Модель whisper один раз на прогон.

    `speak_chunks` зовётся на каждом дубле стендапа и на каждом спикере:
    если грузить модель каждый раз, блок проводит минуты в загрузке весов
    между файлами. Модель тяжёлая, держим одну на прогон.
    """
    key = (model_name, device)
    if key not in _MODELS:
        _MODELS[key] = interview.make_model(model_name, device)
    return _MODELS[key]


def _src_sig(video: Path):
    """Подпись исходника: размер и время. Замена файла ломает кеш."""
    st = video.stat()
    return [int(st.st_size), int(st.st_mtime)]


def load_words(cache: Path, video: Path, model_name: str):
    """Ранее распознанные слова, либо None, если кеша нет или он чужой.

    Повторный прогон иначе заново гоняет whisper по всем дублям стендапа
    и спикерам — минуты ради ответа, который уже известен. Кеш безнадёжно
    устаревает, если сменить модель, поэтому модель входит в ключ.
    """
    if not cache.is_file():
        return None
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("model") != model_name:
        return None
    if data.get("src") != _src_sig(video):
        return None
    if not isinstance(data.get("words"), list):
        return None
    return [interview.Word(w, s, e, p) for w, s, e, p in data["words"]]


def save_words(cache: Path, video: Path, model_name: str, words) -> None:
    """Положить кеш. Пишем через временный файл: обрыв посреди записи
    оставляет полукорректный JSON, а он тогда просто не прочитается и
    файл распознается заново — результат не портится."""
    payload = {"model": model_name, "src": _src_sig(video),
               "words": [[w.w, w.start, w.end, w.prob] for w in words]}
    tmp = cache.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(cache)
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass


def speak_chunks(video: Path, work: Path, model, model_name: str,
                 device: str, max_pause: float, merge_gap: float,
                 min_keep: float):
    """Куски чистой речи из одного файла: [(in, out, text)].

    Возвращает None, если файл не дал речи: значит это не тот дубль.
    """
    wav = work / (video.stem + "_16k.wav")
    cache = work / (video.stem + ".tr.json")
    try:
        words = load_words(cache, video, model_name)
        if words is None:
            if not wav.exists():
                interview.extract_wav(video, wav)
            m, dev = get_model(model_name, device)
            print("  {}: распознаю...".format(video.name))
            words, _ = interview.transcribe_audio_array(
                interview.load_mono16(wav), m, video.stem)
            save_words(cache, video, model_name, words)
        if not words:
            return None
        keeps, _dropped = interview.build_keeps(words, max_pause, merge_gap,
                                                min_keep)
        return [(float(i), float(o), t) for i, o, t in keeps if o - i >= 1.0]
    except Exception as e:
        print("  {}: не разобрать речь ({})".format(video.name, e))
        return None


def pick_best_take(candidates):
    """Из дублей стендапа выбрать лучший.

    Критерий — длиннейший НЕПРЕРЫВНЫЙ кусок, а не общая длина и не
    число кусков. Стендап читают в кадр, и в эфир идёт связная речь
    подряд. Дубль, разбитый на три обрывка по 8 секунд, читается как
    «человек не знает, что сказать», даже если суммарно он длиннее
    дубля с одной связной фразой на 30 секунд. Поэтому сначала смотрим,
    сколько говорится без единого разрыва, и только потом — сколько
    всего.
    """
    best = None
    for name, chunks in candidates:
        if not chunks:
            continue
        longest = max(o - i for i, o, _ in chunks)
        total = sum(o - i for i, o, _ in chunks)
        worst = min(interview.chunk_score(i, o, t) for i, o, t in chunks)
        rank = (longest, total, -worst)
        if best is None or rank > best[0]:
            best = (rank, name, chunks, total)
    return best


def take_window(chunks, target_sec, min_sec):
    """Окно из кусков общей длительностью около target_sec.

    Куски не режутся посередине: если фраза кончилась, обрыв виден
    зрителю. Берём подряд идущие куски и подгоняем по краям.
    """
    if not chunks:
        return None
    chunks = [(float(c[0]), float(c[1]), str(c[2])) for c in chunks]
    # Вырезка без длины бесполезна: Premiere получит in==out и пустой кусок.
    # Мусор лучше пропустить, чем положить в секвенцию.
    chunks = [(i, o, t) for i, o, t in chunks if o - i >= 1.0]
    if not chunks:
        return None
    chunks.sort(key=lambda c: c[0])
    if len(chunks) == 1:
        i, o, txt = chunks[0]
        want = max(min_sec, min(target_sec, o - i))
        return (i, min(o, i + want), txt)

    best = None
    for a in range(len(chunks)):
        for b in range(a, len(chunks)):
            span = chunks[b][1] - chunks[a][0]
            if span > target_sec * 1.35 and b > a:
                break
            if span < min_sec:
                continue
            score = abs(span - target_sec)
            if best is None or score < best[0]:
                best = (score, a, b, span)
    if best is None:
        return None
    _, a, b, _ = best
    return (chunks[a][0], chunks[b][1],
            " ".join(c[2] for c in chunks[a:b + 1]))


def process_standup(folder: Path, work: Path, model_name, device, log,
                    budget=None):
    """Лучший дубль стендапа -> (файл, in, out, текст) либо None."""
    files = videos(folder)
    if not files:
        return None
    target = float(cfg("standup_target_sec", 30.0))
    floor = float(cfg("standup_min_sec", 12.0))
    if budget:
        # Не дать стендапу съесть больше четверти: он голос, а не весь ролик.
        target = min(target, budget)
        floor = min(floor, budget)
    max_pause = float(settings.get("interview.max_pause_sec", 0.6))
    merge_gap = float(settings.get("interview.merge_gap_sec", 1.2))
    min_keep = float(settings.get("interview.min_keep_sec", 1.5))

    log("Стенап: дублей {}".format(len(files)))
    cands = []
    for p in files:
        ch = speak_chunks(p, work, None, model_name, device,
                          max_pause, merge_gap, min_keep)
        if ch:
            cands.append((p, ch))
    best = pick_best_take(cands)
    if not best:
        log("Стенап: ни один дубль не дал разборчивой речи")
        return None
    _rank, path, chunks, total = best
    win = take_window(chunks, target, floor)
    if not win:
        return None
    i, o, text = win
    log("Стенап: {} из {} дублей, речи {:.0f}с, берём {:.0f}-{:.0f}с"
        .format(path.name, len(files), total, i, o))
    return (path, i, o, text)


def process_interviews(folder: Path, work: Path, model_name, device, log,
                       budget=None):
    """По одному ответу на каждого спикера -> [(файл, in, out, текст)]."""
    files = videos(folder)
    if not files:
        return []
    per = float(cfg("interview_answer_sec", 15.0))
    if budget and files:
        # На всех спикеров делим остаток поровну, но не меньше половины
        # ответа: обрезанный на 5с ответ в эфире не читается.
        per = max(per * 0.5, min(per, budget / len(files)))
    target = per
    max_pause = float(settings.get("interview.max_pause_sec", 0.6))
    merge_gap = float(settings.get("interview.merge_gap_sec", 1.2))
    min_keep = float(settings.get("interview.min_keep_sec", 1.5))
    log("Интервью: спикеров {}".format(len(files)))
    out = []
    for p in files:
        ch = speak_chunks(p, work, None, model_name, device,
                          max_pause, merge_gap, min_keep)
        if not ch:
            log("  {}: речи нет, пропускаю".format(p.name))
            continue
        chunks = sorted(ch, key=lambda c: c[0])
        # ответ должен быть ЗАВЕРШЁННЫМ: берём куски подряд, а не один
        # обрывок. Стандарт — 15 секунд, это две-три фразы.
        win = take_window(chunks, target, target * 0.6)
        if not win:
            continue
        i, o, text = win
        log("  {}: {:.0f}-{:.0f}с ({:.0f}с)".format(p.name, i, o, o - i))
        out.append((p, i, o, text))
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
ORDER_KEYS = (
    "digest.rec_duration_sec", "digest.duration_over_tolerance_sec",
    "digest.max_same_plan_run", "digest.max_scale_jump", "digest.order_seed",
    "digest.two_opt_iterations", "digest.digest_finale_wide",
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


def run_autocut(broll_csv: Path, order_csv: Path, target_sec: float, log,
                device="auto"):
    """Порядок нарезки по крупности — это работа autocut.

    Свою раскладку не пишем: score_joint, fix_with_cutaways, fit_target и
    enforce_finale в autocut уже отлажены и покрыты тестами. Дублировать
    их здесь — значит завести две правды, которые разъедутся.
    """
    import subprocess
    key = {"v": 1, "target": float(target_sec), "mode": "digest",
           "broll": hashlib.sha1(broll_csv.read_bytes()).hexdigest(),
           "cfg": settings_sig(ORDER_KEYS)}
    if is_fresh(order_csv, key):
        log("Нарезка: порядок не изменился, беру готовый")
        return order_csv
    cmd = [sys.executable, "-m", "src.modes.autocut", "--csv", str(broll_csv),
           "--mode", "digest", "--auto", "--out", str(order_csv),
           "--target-sec", str(target_sec), "--device", device]
    log("Нарезка: подбираю порядок под {:.0f}с".format(target_sec))
    r = subprocess.run(cmd, cwd=str(SRC.parent), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        log("Нарезка: autocut упал (код {})".format(r.returncode))
        for line in (r.stderr or r.stdout or "").splitlines()[-6:]:
            log("   " + line)
        return None
    try:
        with order_csv.open(encoding="utf-8") as f:
            n = len(list(csv.DictReader(f)))
    except Exception:
        n = 0
    log("Нарезка: в порядке кадров {}".format(n))
    mark_fresh(order_csv, key)
    return order_csv


def read_order(order_csv: Path, broll_dir: Path):
    """Строки нарезки -> [(part, файл, in, out, описание)].

    Кусок нарезки берётся целиком, поэтому in=0, out=длительность.
    """
    rows = []
    with order_csv.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            name = (r.get("file") or "").strip()
            if not name:
                continue
            try:
                dur = float((r.get("duration") or "0").replace(",", "."))
            except ValueError:
                dur = 0.0
            path = broll_dir / name
            rows.append(("нарезка", path, 0.0, dur,
                         (r.get("desc") or "").strip()))
    return rows


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
    # Бюджет делится на три части. Нарезка — середина ролика и берёт
    # половину: стендап и интервью это голос, нарезка — то, о чём он
    # говорит. Стандарт (слайд 2) ограничивает только общий хронометраж.
    st_budget = target * 0.25
    iv_budget = target * 0.25
    nar_budget = target - st_budget - iv_budget
    log("Хронометраж {:.0f}с: стендап {:.0f}, нарезка {:.0f}, интервью {:.0f}"
        .format(target, st_budget, nar_budget, iv_budget))

    # ---- стендап: один лучший дубль, спереди ----
    front = []
    st = process_standup(parts["standup"], work, args.model, args.device, log,
                         budget=st_budget)
    if st:
        front.append(st)
    else:
        log("Стенап: не собрался, ролик начнётся с нарезки")

    # ---- нарезка: существующий detect + autocut ----
    middle = []
    b_path = work / "broll.csv"
    order_csv = work / "nar_order.csv"
    if broll_csv(parts["broll"], b_path, log, args.device):
        if run_autocut(b_path, order_csv, nar_budget, log, args.device):
            middle = read_order(order_csv, parts["broll"])

    # ---- интервью: по 15с на спикера, в конце ----
    back = process_interviews(parts["interview"], work, args.model, args.device,
                              log, budget=iv_budget)

    # ---- порядок секвенции: стендап -> нарезка -> интервью ----
    rows = []
    for path, i, o, text in front:
        rows.append(("стендап", path, i, o, text[:200]))
    rows.extend(middle)
    for path, i, o, text in back:
        rows.append(("интервью", path, i, o, text[:200]))

    if not rows:
        print("Собрать нечего: все три части пустые.")
        return 1

    plan = write_plan(rows, out_dir / "plan.csv", base)
    total = sum(o - i for _p, _f, i, o, _d in rows)
    log("\nПлан: " + str(plan))
    log("Кусов: {} (стендап {}, нарезка {}, интервью {})".format(
        len(rows), len(front), len(middle), len(back)))
    log("Хронометраж: {:.1f}с (цель {:.0f}с)".format(total, target))
    if total > target:
        log("  ПРЕВЫШЕНИЕ на {:.1f}с".format(total - target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
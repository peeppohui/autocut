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
    """Папки трёх частей. Если материала нет, папка возвращается пустой."""
    names = sorter.part_names()
    return {k: (root / names[k] if (root / names[k]).is_dir() else None)
            for k in ("standup", "broll", "interview")}


def videos(folder: Path):
    if folder is None or not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in sorter.VIDEO_EXT)


# ---------------------------------------------------------------- речь


def speak_chunks(video: Path, work: Path, model, model_name: str,
                 device: str, max_pause: float, merge_gap: float,
                 min_keep: float):
    """Куски чистой речи из одного файла: [(in, out, text)].

    Возвращает None, если файл не дал речи: значит это не тот дубль.
    """
    wav = work / (video.stem + "_16k.wav")
    try:
        if not wav.exists():
            interview.extract_wav(video, wav)
        words, _ = interview.transcribe(wav, model_name, device)
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
    """
    import subprocess
    files = videos(folder)
    if not files:
        log("Нарезка: папка пуста")
        return None
    out_csv.parent.mkdir(parents=True, exist_ok=True)
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
    return out_csv


# ---------------------------------------------------------------- сборка


def write_plan(rows, out_csv: Path):
    """rows: [(часть, файл, in, out, текст/описание)] -> CSV для Premiere.

    Колонка `path` — путь файла ОТНОСИТЕЛЬНО корня с частями. Premiere
    ищет медиа в media-dir, а нарезка лежит в исх\нарезка, стендап в
    исх\стендап: если записать только имя, файл не найдётся и Premiere
    попросит relink. Относительный путь premiere.py собирает сам.
    """
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["n", "file", "part", "path", "in", "out", "duration",
                    "desc"])
        for i, (part, path, src_in, src_out, desc) in enumerate(rows, 1):
            # части уже несут подпапку в .parent, но если файл лежит в
            # корне (как при --check) — просто имя
            try:
                rel = path.relative_to(path.parents[1]).as_posix()
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

    root = Path(args.input).expanduser()
    if not root.is_dir():
        print("Нет папки: " + str(root))
        return 1
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

    plan = write_plan(rows, out_dir / "plan.csv")
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
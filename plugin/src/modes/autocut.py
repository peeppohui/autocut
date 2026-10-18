#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoCut — помощник монтажа дайджестов и интервью для медиацентра.
Расставляет и проверяет кадры по классическим правилам склейки по крупности.

Шкала крупности (по Кулешову / Соколову, классика):
  0 деталь    — часть лица, предмет, руки, глаза
  1 крупный   — голова в кадре почти полностью
  2 средний1  — человек по пояс (первый средний)
  3 средний2  — человек по колени (второй средний, «американский»)
  4 общий     — человек в полный рост + окружение
  5 дальний   — человек мал в кадре, адресный план

Главное правило (комфортный монтаж):
  Идеально — склейка ЧЕРЕЗ ОДИН план: |a-b| == 2
  Исключения: крупный+деталь, общий+дальний (|a-b|==1, но разрешено)
  Плохо: соседние (|a-b|==1) — «рывок»
  Плохо: одинаковые (|a-b|==0) — jump cut
  Рискованно: |a-b| >= 3 — дезориентация (допустимо только на смене объекта/сцены)

Источники: Кулешов, Соколов «Монтаж», theway-pro, workin3d, kurshub.
"""

from __future__ import annotations
import argparse
import csv
import itertools
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bootstrap  # noqa: F401  — регистрирует src/config в sys.path
import settings

import narrative

try:
    # чтобы русский текст не падал на Windows-консоли (cp1251)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---------------------------------------------------------------- шкала

PLAN_ORDER: Dict[str, int] = {
    "деталь": 0,
    "крупный": 1,
    "средний1": 2,
    "средний2": 3,
    "общий": 4,
    "дальний": 5,
}

PLAN_LABEL = {v: k for k, v in PLAN_ORDER.items()}

# Синонимы, как люди реально пишут в таблице
SYNONYMS = {
    "деталь": "деталь",
    "деталка": "деталь",
    "макро": "деталь",
    "ecu": "деталь",
    "сверхкрупный": "деталь",
    "чокер": "деталь",
    "крупный": "крупный",
    "ку": "крупный",
    "cu": "крупный",
    "лицо": "крупный",
    "средний1": "средний1",
    "средний 1": "средний1",
    "первый средний": "средний1",
    "1 средний": "средний1",
    "по пояс": "средний1",
    "поясной": "средний1",
    "mcu": "средний1",
    "средний2": "средний2",
    "средний 2": "средний2",
    "второй средний": "средний2",
    "по колени": "средний2",
    "по колено": "средний2",
    "американский": "средний2",
    "ms": "средний2",
    "общий": "общий",
    "полный рост": "общий",
    "ls": "общий",
    "дальний": "дальний",
    "адресный": "дальний",
    "els": "дальний",
    "панорама": "дальний",
}


def normalize_plan(raw: str) -> str:
    key = raw.strip().lower().replace("ё", "е")
    # убрать лишние пробелы
    key = " ".join(key.split())
    if key in SYNONYMS:
        return SYNONYMS[key]
    # попробовать без цифры-пробела
    nospace = key.replace(" ", "")
    if nospace in SYNONYMS:
        return SYNONYMS[nospace]
    raise ValueError(f"Неизвестный план «{raw}». Допустимо: деталь, крупный, средний1, средний2, общий, дальний")


@dataclass
class Clip:
    file: str
    plan: str  # нормализованный
    hero: str = "герой"  # кто/что в кадре. Одинаковый hero = один объект -> строгое правило
    duration: float = 3.0
    desc: str = ""
    # Речь, если куски прогонялись через распознавание. Пусто - а
    # значит работаем только по картинке, и это нормальный режим:
    # распознавать папку видео дорого, а дайджестам текст часто не нужен.
    text: str = ""

    @property
    def size(self) -> int:
        return PLAN_ORDER[self.plan]


# ------------------------------------------------------- оценка склейки

@dataclass
class Joint:
    score: int  # 0..100
    status: str  # ОТЛИЧНО / ХОРОШО / ДОПУСТИМО / ПЛОХО
    comment: str


def score_joint(a: Clip, b: Clip) -> Joint:
    """Оценка одной склейки a -> b."""
    # Разные объекты / разные герои — правило крупности почти не действует
    if a.hero.strip().lower() != b.hero.strip().lower():
        # всё равно ругаем откровенный jump cut одинаковых пустых планов подряд
        if a.plan == b.plan and a.plan in ("общий", "дальний"):
            return Joint(70, "ДОПУСТИМО",
                         f"Разные объекты, но два «{a.plan}» подряд — проверьте, нет ли ощущения топтания. Лучше разбить средним/крупным.")
        return Joint(90, "ХОРОШО", f"Разные объекты ({a.hero} -> {b.hero}): смена читается, крупность не критична.")

    d = abs(a.size - b.size)
    pair = tuple(sorted((a.plan, b.plan)))

    # Исключения классики
    if pair == ("деталь", "крупный"):
        return Joint(95, "ОТЛИЧНО", "Исключение из правила: крупный + деталь монтируются отлично.")
    if pair == ("дальний", "общий"):
        return Joint(95, "ОТЛИЧНО", "Исключение из правила: общий + дальний монтируются отлично.")

    if d == 0:
        return Joint(20, "ПЛОХО",
                     f"Одинаковые планы «{a.plan} -> {b.plan}» на одном герое = jump cut / рывок. "
                     f"Вставьте перебивку (деталь/другой ракурс) или смените крупность через один.")
    if d == 1:
        return Joint(40, "ПЛОХО",
                     f"Соседние планы «{a.plan} -> {b.plan}» — классический рывок вперёд/назад. "
                     f"Нужно через один (напр. {a.plan} -> {PLAN_LABEL.get(a.size+2 if a.size+2<=5 else a.size-2, '?')}).")
    if d == 2:
        return Joint(100, "ОТЛИЧНО", f"Идеально через один: {a.plan} -> {b.plan}.")
    if d == 3:
        return Joint(70, "ДОПУСТИМО",
                     f"Прыжок через два ({a.plan} -> {b.plan}): допустимо при смене акцента, но мягче было бы через один. "
                     f"Держите план подольше, добавьте движение/звуковую связку (J/L-cut).")
    # d >= 4
    if d > MAX_SCALE_JUMP:
        return Joint(30, "ПЛОХО",
                     f"Негармоничный перепад ({a.plan} -> {b.plan}): это {d} ступеней "
                     f"по шкале Кулешова при пороге {MAX_SCALE_JUMP}. Так близкая "
                     f"деталь с дальним видом и не монтируется. Ведите ступенями "
                     f"или разбейте перебивкой.")
    return Joint(50, "ДОПУСТИМО",
                 f"Резкий перепад ({a.plan} -> {b.plan}): зритель может потерять связь. "
                 f"Допустимо только как адресный переход (дальний->действие) или акцент. "
                 f"Лучше идти ступенями.")


# Рекомендуемые длительности для дайджеста (сек) — из config/settings.json
REC_DURATION: Dict[str, Tuple[float, float]] = {
    plan: (float(lo), float(hi))
    for plan, (lo, hi) in settings.get("digest.rec_duration_sec", {}).items()
}

# Запас сверх верхней границы, прежде чем ругаться на длительность
DURATION_TOLERANCE = float(settings.get("digest.duration_over_tolerance_sec", 2.0))

# Сколько одинаковых планов подряд — уже монотонно и пора бить перебивкой
MAX_SAME_PLAN_RUN = int(settings.get("digest.max_same_plan_run", 3))

# Насколько резко можно переходить между крупностями. По стандарту
# медиацентра (слайд «Монтаж по крупности») скачок не должен быть большим,
# и соединение близкой детали с дальним видом названо негармоничным. На
# шкале Кулешова это разница 4, поэтому порог 3: всё, что прыгает
# дальше, попадает в отчёт как проблема, а не как «допустимо».
MAX_SCALE_JUMP = int(settings.get("digest.max_scale_jump", 3))

# Порядок перебора всегда детерминирован: один и тот же CSV даёт одну и ту же рыбу
ORDER_SEED = int(settings.get("digest.order_seed", 42))
TWO_OPT_ITERATIONS = int(settings.get("digest.two_opt_iterations", 2000))


def check_duration(c: Clip) -> Optional[str]:
    lo, hi = REC_DURATION[c.plan]
    if c.duration < lo - 0.01:
        return f"«{c.file}» ({c.plan}, {c.duration}с): коротковат — {c.plan} обычно держат {lo}–{hi}с, зритель не успеет считать."
    if c.duration > hi + DURATION_TOLERANCE:  # запас, чтобы не ворчать
        return f"«{c.file}» ({c.plan}, {c.duration}с): затянут — {c.plan} обычно {lo}–{hi}с. Проверьте ритм."
    return None


def validate_sequence(clips: List[Clip], mode: str = "digest") -> Dict:
    """Полная проверка готовой последовательности. Возвращает отчет."""
    joints: List[Joint] = []
    problems: List[str] = []
    warnings: List[str] = []

    for i in range(len(clips) - 1):
        j = score_joint(clips[i], clips[i + 1])
        joints.append(j)
        if j.status == "ПЛОХО":
            problems.append(f"Склейка {i+1}→{i+2}: {clips[i].file} [{clips[i].plan}] → {clips[i+1].file} [{clips[i+1].plan}]. {j.comment}")
        elif j.status == "ДОПУСТИМО":
            warnings.append(f"Склейка {i+1}→{i+2}: {clips[i].file} → {clips[i+1].file}. {j.comment}")

    # длительности
    for c in clips:
        w = check_duration(c)
        if w:
            warnings.append(w)

    # не больше 3 одинаковых подряд на одном герое
    run = 1
    for i in range(1, len(clips)):
        if clips[i].plan == clips[i-1].plan and clips[i].hero == clips[i-1].hero:
            run += 1
            if run >= MAX_SAME_PLAN_RUN:
                problems.append(f"Кадры {i}–{i+2}: три одинаковых «{clips[i].plan}» подряд на герое «{clips[i].hero}» — монотонно, разбейте перебивкой.")
        else:
            run = 1

    # дайджест должен начинаться с установочного
    if mode == "digest" and clips:
        if clips[0].plan not in ("общий", "дальний", "средний2"):
            warnings.append(f"Начало с «{clips[0].plan}» — для дайджеста лучше открыться общим/дальним (адресный план: где мы?).")
        # деталь без подводки?
        for i, c in enumerate(clips):
            if c.plan == "деталь" and i == 0:
                warnings.append("Деталь первым кадром без контекста — зритель не поймёт, часть чего это. Дайте сначала общий/средний.")

    # интервью: парность крупностей диалога
    if mode == "interview":
        heroes = {c.hero for c in clips}
        if len(heroes) >= 2:
            # собираем крупности по героям
            by_hero: Dict[str, set] = {}
            for c in clips:
                by_hero.setdefault(c.hero, set()).add(c.plan)
            # если один герой всё время крупно, а второй мелко — дисбаланс
            sizes = {h: sum(PLAN_ORDER[p] for p in ps) / len(ps) for h, ps in by_hero.items()}
            if max(sizes.values()) - min(sizes.values()) >= 2:
                warnings.append(f"Дисбаланс парных крупностей в диалоге: {sizes}. "
                                f"Герои должны идти в близких крупностях (оба средний1/средний2), иначе один «ближе» зрителю.")

    total = sum(j.score for j in joints) / len(joints) if joints else 100
    grade = "ОТЛИЧНО" if total >= 85 else "ХОРОШО" if total >= 70 else "ТРЕБУЕТ ПРАВОК" if total >= 55 else "ПЛОХО"
    return {
        "total": round(total, 1),
        "grade": grade,
        "joints": joints,
        "problems": problems,
        "warnings": warnings,
    }


# ------------------------------------------------------- авто-раскладка

def chain_link(order: List[Clip]) -> float:
    """Насколько куски связаны по смыслу, 0..100.

    Считается только когда есть текст. Без распознавания речи дайджест
    работает ровно как раньше: единственное, что известно о куске, это
    его план и герой, а по ним смысловую связность не вывести.
    """
    if len(order) < 2 or not any(c.text for c in order):
        return 0.0
    idx = narrative.Index([c.text or c.desc for c in order])
    vals = []
    for i in range(len(order) - 1):
        if not (order[i].text and order[i + 1].text):
            continue
        vals.append(narrative.link_score(order[i].text, order[i + 1].text, idx).score)
    if not vals:
        return 0.0
    return 100.0 * sum(vals) / len(vals)


def transcribe_clips(clips: List[Clip], folder: Path,
                     model_name: str = "", device: str = "auto") -> int:
    """Заполняет Clip.text речью из кусков. Возвращает число распознанных.

    Отдельный шаг и отдельный флаг не из лени: распознавание папки видео
    на два часа занимает минуты, а большая часть дайджестов собирается
    вообще без единого слова. Кто не просил - тот не платит временем.
    """
    import interview
    model_name = model_name or str(settings.get("interview.model", "base"))
    try:
        model, dev = interview.make_model(model_name, device)
    except Exception as e:
        print(f"Распознавание пропущено: {str(e)[:140]}")
        return 0
    print(f"Распознаю речь в {len(clips)} кусках ({model_name}, {dev})...")
    done = 0
    for c in clips:
        p = Path(folder) / c.file
        if not p.is_file():
            continue
        wav = Path(folder) / f"._sp_{done}.wav"
        try:
            interview.extract_wav(p, wav)
            audio = interview.load_mono16(wav)
            _words, segs = interview.transcribe_audio_array(audio, model, c.file)
            c.text = " ".join((s.get("text") or "").strip() for s in segs).strip()
            if c.text:
                done += 1
                print(f"  {c.file}: {c.text[:70]}")
            else:
                print(f"  {c.file}: речи не найдено")
        except Exception as e:
            print(f"  {c.file}: не разобрано ({str(e)[:80]})")
        finally:
            wav.unlink(missing_ok=True)
    print(f"Распознано кусков с речью: {done} из {len(clips)}")
    return done


def total_score(order: List[Clip]) -> float:
    if len(order) < 2:
        return 100.0
    s = sum(score_joint(order[i], order[i+1]).score for i in range(len(order)-1))
    # бонус за старт с общего в дайджесте
    if order and order[0].plan in ("общий", "дальний"):
        s += 5
    base = s / (len(order) - 1)
    # смысловая связность. Без текста она равна нулю и ничего не портит.
    w = float(settings.get("narrative.chain_weight", 0.0))
    if w and narrative.enabled():
        base += w * chain_link(order)
    return base


def suggest_order(clips: List[Clip], mode: str = "digest", seed: int = None) -> List[Clip]:
    """Ищет порядок с максимальным комфортом склеек. Не меняет сами клипы, только порядок."""
    if len(clips) <= 1:
        return list(clips)
    rnd = random.Random(ORDER_SEED if seed is None else seed)

    # Маленький набор — точный перебор
    if len(clips) <= 8:
        best, best_s = None, -1.0
        for perm in itertools.permutations(clips):
            # для дайджеста требуем старт с общего/дальнего, если он есть в наборе
            if mode == "digest":
                has_wide = any(c.plan in ("общий", "дальний") for c in clips)
                if has_wide and perm[0].plan not in ("общий", "дальний", "средний2"):
                    continue
            s = total_score(list(perm))
            if s > best_s:
                best_s, best = s, list(perm)
        return list(best) if best else list(clips)

    # Большой набор — жадный старт + 2-opt улучшение
    # старт: общий первым, дальше каждый раз лучший следующий
    rest = list(clips)
    wide = [c for c in rest if c.plan in ("общий", "дальний")]
    start = max(wide, key=lambda c: c.duration) if (wide and mode == "digest") else rnd.choice(rest)
    order = [start]
    rest.remove(start)
    while rest:
        # выбираем следующий с лучшим скором, с небольшой случайностью чтобы не зациклиться
        scored = sorted(rest, key=lambda c: score_joint(order[-1], c).score, reverse=True)
        # берем из топ-3 случайно
        nxt = rnd.choice(scored[:min(3, len(scored))])
        order.append(nxt)
        rest.remove(nxt)

    # 2-opt hill climbing
    best_s = total_score(order)
    for _ in range(TWO_OPT_ITERATIONS):
        i, j = sorted(rnd.sample(range(len(order)), 2))
        cand = order[:i] + list(reversed(order[i:j+1])) + order[j+1:]
        if mode == "digest" and any(c.plan in ("общий", "дальний") for c in clips):
            if cand[0].plan not in ("общий", "дальний", "средний2"):
                continue
        s = total_score(cand)
        if s > best_s:
            order, best_s = cand, s
    return order


def fix_with_cutaways(order: List[Clip], pool: List[Clip]) -> Tuple[List[Clip], List[str]]:
    """Пытается починить плохие стыки вставкой перебивки из пула (детали/другой герой)."""
    notes: List[str] = []
    if not pool:
        return order, notes
    out: List[Clip] = [order[0]]
    for nxt in order[1:]:
        j = score_joint(out[-1], nxt)
        if j.status == "ПЛОХО":
            # ищем перебивку, которая хорошо стыкуется с обеими сторонами
            best_cb, best_s = None, -1
            for cb in pool:
                s = score_joint(out[-1], cb).score + score_joint(cb, nxt).score
                if s > best_s:
                    best_s, best_cb = s, cb
            if best_cb and best_s / 2 >= 70:
                out.append(best_cb)
                notes.append(f"Между «{out[-2].file}» и «{nxt.file}» вставлена перебивка «{best_cb.file}» [{best_cb.plan}] — иначе был бы рывок.")
        out.append(nxt)
    return out, notes


# ------------------------------------------------------- ввод/вывод

def load_csv(path: Path) -> List[Clip]:
    clips: List[Clip] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        # поддерживаем русские заголовки
        def col(*names):
            for n in names:
                if n in (reader.fieldnames or []):
                    return n
            return None
        c_file = col("file", "файл", "клип", "name")
        c_plan = col("plan", "план", "крупность")
        c_hero = col("hero", "герой", "объект", "кто")
        c_dur = col("duration", "длительность", "длит", "сек")
        c_desc = col("desc", "описание", "коммент")
        if not c_file or not c_plan:
            raise ValueError(f"В CSV нужны колонки file и plan. Нашёл: {reader.fieldnames}")
        for row in reader:
            if not (row.get(c_file) or "").strip():
                continue
            clips.append(Clip(
                file=row[c_file].strip(),
                plan=normalize_plan(row[c_plan]),
                hero=(row.get(c_hero) or "герой").strip() or "герой",
                duration=float((row.get(c_dur) or "3").replace(",", ".") or 3),
                desc=(row.get(c_desc) or "").strip(),
            ))
    return clips


def print_report(clips: List[Clip], report: Dict, notes: List[str] | None = None):
    print("=" * 64)
    print(f"OCENKA MONTAZHA: {report['total']} / 100 — {report['grade']}")
    print("=" * 64)
    for i, c in enumerate(clips, 1):
        print(f"{i:2d}. {c.file}  [{c.plan} | {c.hero} | {c.duration}s] {c.desc}")
        if i <= len(report["joints"]):
            j = report["joints"][i-1]
            mark = {"ОТЛИЧНО": "[OK]", "ХОРОШО": "[~]", "ДОПУСТИМО": "[!]", "ПЛОХО": "[X]"}[j.status]
            print(f"    {mark} -> {j.status} ({j.score}): {j.comment}")
    print("-" * 64)
    if report["problems"]:
        print("KRITICHNO:")
        for p in report["problems"]:
            print("  [X] " + p)
    if report["warnings"]:
        print("ZAMECHANIYA:")
        for w in report["warnings"]:
            print("  [!] " + w)
    if notes:
        print("VSTAVKI:")
        for n in notes:
            print("  [+] " + n)
    if not report["problems"] and not report["warnings"]:
        print("Chisto. Sklejki komfortnye.")
    print("=" * 64)


def save_timeline_csv(clips: List[Clip], path: Path):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["n", "file", "plan", "hero", "duration", "desc"])
        for i, c in enumerate(clips, 1):
            w.writerow([i, c.file, c.plan, c.hero, c.duration, c.desc])
    print(f"Таймлайн сохранён: {path}")


def enforce_finale(order: List[Clip], mode: str = "digest") -> Tuple[List[Clip], str]:
    """Шаблон дайджеста: финал — общим/дальним (точка, а не обрыв).
    Переставляет подходящий широкий в конец, если комфорт почти не падает."""
    if mode != "digest" or len(order) < 3 or order[-1].plan in ("общий", "дальний"):
        return order, ""
    base = total_score(order)
    for i in range(1, len(order) - 1):  # открывающий кадр не трогаем
        c = order[i]
        if c.plan in ("общий", "дальний"):
            cand = order[:i] + order[i + 1:] + [c]
            if total_score(cand) >= base - 5:
                return cand, f"Финал: «{c.file}» [{c.plan}] переставлен в конец (точка общим)."
    return order, ""


def fit_target(order: List[Clip], target_sec: float) -> Tuple[List[Clip], List[str]]:
    """Ужать набор под хронометраж: выкидываем куски по одному — тот, чьё
    удаление меньше всего роняет комфорт (открывающий кадр не трогаем)."""
    notes: List[str] = []
    order = list(order)
    while len(order) > 2 and sum(c.duration for c in order) > target_sec:
        base = total_score(order)
        best_i, best_loss = -1, float("inf")
        for i in range(1, len(order)):
            cand = order[:i] + order[i + 1:]
            loss = base - total_score(cand)
            # длинные куски чуть охотнее под нож (их и так урежет --trim-rec)
            loss -= min(order[i].duration, 10) * 0.05
            if loss < best_loss:
                best_loss, best_i = loss, i
        dropped = order.pop(best_i)
        notes.append(f"Минус «{dropped.file}» [{dropped.plan}, {dropped.duration}с] ради {target_sec}с.")
    return order, notes


def demo_clips(kind: str) -> List[Clip]:
    if kind == "interview":
        return [
            Clip("int_01_wide.mp4", "общий", "гость+ведущий", 5),
            Clip("int_02_guest_m1.mp4", "средний1", "гость", 4),
            Clip("int_03_host_m1.mp4", "средний1", "ведущий", 3),
            Clip("int_04_guest_cu.mp4", "крупный", "гость", 3),
            Clip("int_05_hands.mp4", "деталь", "гость", 2, "руки/деталь-перебивка"),
            Clip("int_06_guest_m2.mp4", "средний2", "гость", 4),
        ]
    # digest по умолчанию
    return [
        Clip("ev_01_hall_wide.mp4", "общий", "зал", 6),
        Clip("ev_02_speaker_m2.mp4", "средний2", "спикер", 4),
        Clip("ev_03_speaker_cu.mp4", "крупный", "спикер", 3),
        Clip("ev_04_audience_m1.mp4", "средний1", "зрители", 3),
        Clip("ev_05_hands_detail.mp4", "деталь", "спикер", 2),
        Clip("ev_06_stage_general.mp4", "общий", "сцена", 5),
        Clip("ev_07_applause_m1.mp4", "средний1", "зрители", 3),
    ]


def main(argv=None):
    ap = argparse.ArgumentParser(description="AutoCut — проверка и раскладка кадров по крупности")
    ap.add_argument("--csv", help="входной CSV (file,plan,hero,duration,desc)")
    ap.add_argument("--mode", choices=["digest", "interview"], default="digest", help="дайджест или интервью")
    ap.add_argument("--auto", action="store_true", help="авто-подобрать лучший порядок")
    ap.add_argument("--demo", choices=["digest", "interview"], help="запустить на встроенном примере")
    ap.add_argument("--out", help="сохранить таймлайн в CSV")
    ap.add_argument("--target-sec", type=float, default=None,
                    help="шаблон дайджеста: ужать набор под хронометраж. "
                         "Пусто — берётся digest.target_sec (по стандарту "
                         "медиацентра 120с), 0 = не ужимать")
    ap.add_argument("--finale-wide", action="store_true",
                    default=bool(settings.get("digest.digest_finale_wide", True)),
                    help="шаблон дайджеста: финал обязательно общим/дальним")
    ap.add_argument("--transcribe", action="store_true",
                    help="распознать речь в кусках, чтобы выстраивать цепочку "
                         "по смыслу. Дорого: минуты на папку видео")
    ap.add_argument("--device", default="auto")
    args = ap.parse_args(argv)

    if args.demo:
        clips = demo_clips(args.demo)
        mode = args.demo == "interview" and "interview" or "digest"
        print(f"Демо-режим: {args.demo} ({len(clips)} клипов)")
    elif args.csv:
        clips = load_csv(Path(args.csv))
        mode = args.mode
        print(f"Загружено {len(clips)} клипов из {args.csv}, режим: {mode}")
    else:
        ap.print_help()
        print("\nПример: python plugin/src/modes/autocut.py --demo digest --auto")
        print("       python plugin/src/modes/autocut.py --csv clips.csv --mode digest --auto --out timeline.csv")
        return 0

    # Речь нужна только для смысловой связности. Без флага куски остаются
    # без текста, и раскладка считается ровно как раньше - только по
    # крупности. Это не «ухудшенный режим», а обычный.
    if args.transcribe and narrative.enabled():
        folder = Path(args.csv).parent if args.csv else Path(".")
        if not transcribe_clips(clips, folder, device=args.device):
            print("  Текста не распозналось - цепочка будет строиться по картинке.")
    elif args.transcribe:
        print("Повествование выключено в настройках, распознавание пропущено.")

    if args.auto or args.demo:
        ordered = suggest_order(clips, mode=mode)
        # pool перебивок = детали + герои, которых нет в проблемном стыке
        pool = [c for c in clips if c.plan == "деталь"]
        fixed, notes = fix_with_cutaways(ordered, pool)
        notes = list(notes)
        target = args.target_sec
        if target is None:
            target = float(settings.get("digest.target_sec", 0) or 0)
        if target:
            # сначала урезка (открывающий кадр защищён), потом финал из выживших
            fixed, drop_notes = fit_target(fixed, target)
            notes.extend(drop_notes)
        if args.finale_wide:
            fixed, msg = enforce_finale(fixed, mode=mode)
            if msg:
                notes.append(msg)
        # fix может дублировать деталь — убираем дубли по file, если деталь уже использована дважды
        report = validate_sequence(fixed, mode=mode)
        print_report(fixed, report, notes)
        if any(c.text for c in fixed):
            link = chain_link(fixed)
            print(f"\nСмысловая связность цепочки: {link:.0f} из 100")
            if link < 25:
                print("  Связность низкая: куски почти не продолжают друг "
                      "друга. Возможно, материал разнородный.")
    else:
        report = validate_sequence(clips, mode=mode)
        print_report(clips, report)

    if args.out:
        target = fixed if (args.auto or args.demo) else clips
        save_timeline_csv(target, Path(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

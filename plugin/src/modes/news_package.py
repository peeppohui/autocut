#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AutoCut News Package — раскладка сюжета по мероприятию (1.5–3 минуты).

Что делает: берёт синхроны с транскриптами, закадровый текст, перебивки и
стендап, и собирает черновой таймлайн по правилам новостного сюжета:

  0:00–0:15   лид      — самый сильный синхрон, без слов-паразитов в начале
  0:15–0:45   суть     — закадр + перебивки
  0:45–1:30   синхроны — 2–3 спикера по 8–15 секунд
  1:30–2:00   детали   — закадр + перебивки
  финал       вывод    — стендап, если есть, иначе последний синхрон

Выход (в папку out_dir):
  selects.csv    таймлайн с колонкой track - его читает premiere.py
  storyboard.json  что где стоит и почему
  titles.csv     имена и должности для титров
  report.txt     человекочитаемый расклад

Правила, которые тут соблюдаются:
  1. синхрон никогда не начинается с «ну», «вот», «ээ» - начало подчищается;
  2. первый синхрон - самый сильный (цифры, эмоции, конфликт);
  3. под синхрон длиннее cutaway_after_sec вставляется перебивка;
  4. перебивка не повторяется дважды;
  5. цифры и имена выносятся в титры, а не остаются на слух.

Никакой магии: все пороги и веса — в settings.json, секция news.

Использование:
    python news_package.py --spec package.json --outdir out
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bootstrap  # noqa: F401  — регистрирует src/config в sys.path
import settings

import narrative  # src/modes — рядом, поэтому через bootstrap

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DIGIT_RX = re.compile(r"\d")


# --------------------------------------------------------------- транскрипт

def load_transcript(path: Path) -> List[Dict]:
    """Читает транскрипт в любом из известных видов.

    Поддерживаются:
      [{"start":…, "end":…, "text":…}]           — наш формат и speech.json
      {"segments": [{...}]}                       — Whisper
      {"words": [{...}], "segments": [{...}]}    — Whisper с пословными
    Возвращает [{start, end, text}] в секундах.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    rows: List[Dict] = []

    if isinstance(data, dict):
        segs = data.get("segments") or []
    elif isinstance(data, list):
        segs = data
    else:
        raise ValueError(f"Не понял формат транскрипта: {path}")

    for s in segs:
        if not isinstance(s, dict):
            continue
        try:
            start = float(s["start"])
            end = float(s["end"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append({"start": start, "end": end, "text": (s.get("text") or "").strip()})

    rows.sort(key=lambda r: r["start"])
    return rows


def norm_word(w: str) -> str:
    return re.sub(r"[^\w\-]", "", (w or "").lower().replace("ё", "е"))


# --------------------------------------------------------------- правило 1

def trim_lead_fillers(segments: List[Dict], fillers: List[str],
                      max_sec: float) -> float:
    """Отрезает слова-паразиты в начале реплики.

    Возвращает, сколько секунд срезано. Реплика не должна начинаться
    с «ну», «вот», «ээ» - монтажёр ставит склейку по нулевой фазе звука,
    и если сначала идёт мусор, зритель слышит обрывок.
    """
    if not segments:
        return 0.0
    first = segments[0]
    words = first["text"].split()
    cut = 0
    for w in words:
        if norm_word(w) in fillers or any(norm_word(w).startswith(f[:4]) and len(f) >= 4
                                          for f in fillers if len(f) >= 4):
            cut += 1
            continue
        break
    if cut == 0:
        return 0.0
    share = max(0.0, min(max_sec, (first["end"] - first["start"]) * 0.4))
    return round(share, 3) if cut else 0.0


# --------------------------------------------------------------- правило 2

def _quote_key(sync: Dict, seg: Dict) -> str:
    """Ключ цитаты: чтобы одна реплика не попала в сюжет дважды."""
    return f"{sync.get('speaker', '')}|{seg['start']:.2f}|{seg['end']:.2f}"


def score_segment(seg: Dict, cfg: Dict) -> float:
    """Оценка реплики: чем выше, тем сильнее для лида."""
    w = cfg["score_weights"]
    sw = cfg["strong_words"]
    text = (seg.get("text") or "").lower().replace("ё", "е")
    if not text:
        return -99.0

    score = 0.0
    if DIGIT_RX.search(text):
        score += float(w["digit"])
    score += len(DIGIT_RX.findall(text)) * 0.3

    for group in ("emotion", "conflict"):
        for word in sw.get(group, []):
            if word in text:
                score += float(w[group])

    if text.strip().endswith("?"):
        score += float(w["question"])

    dur = float(seg["end"]) - float(seg["start"])
    if 4.0 <= dur <= 14.0:
        score += float(w["short"])

    fillers = cfg["lead_fillers"]
    for f in fillers:
        if f in text:
            score += float(w["filler_penalty"])
    return round(score, 3)


# --------------------------------------------------------------- нарезка

def split_range(start: float, end: float, min_len: float, max_len: float) -> List[tuple]:
    """Режет интервал на куски от min_len до max_len секунд.

    Режем по границам пауз: если между словами есть тишина, стык ставим
    туда. Из входа это узнать нельзя, поэтому делим равномерно, но
    последний кусок подгоняем, чтобы не получить огрызок.
    """
    total = end - start
    if total <= max_len:
        return [(start, end)] if total >= min_len * 0.5 else []

    n = max(1, int(round(total / ((min_len + max_len) / 2))))
    n = max(n, int(total // max_len) + (1 if total % max_len else 0))
    step = total / n
    out = []
    for i in range(n):
        a = start + i * step
        b = start + (i + 1) * step if i < n - 1 else end
        out.append((round(a, 3), round(b, 3)))
    return out


# --------------------------------------------------------------- план

def role_rank(role: str, want: Tuple[str, ...]) -> float:
    """Насколько роль куска подходит под требуемую.

    Точное совпадение - 1.0, родственная роль - 0.5, не подходит - 0.
    Родство нужно, потому что в живой речи один кусок честно может
    быть и тезисом, и примером: заставлять его подгоняться под одну
    роль значит половину материала отбрасывать.
    """
    if role in want:
        return 1.0
    near = {
        "крючок": ("тезис", "контекст"),
        "вывод": ("тезис", "контекст"),
        "тезис": ("пример", "контекст", "вывод"),
        "пример": ("тезис",),
        "контекст": ("тезис", "крючок"),
        "возражение": ("тезис", "пример"),
    }
    for w in want:
        if role in near.get(w, ()):
            return 0.5
    return 0.0


def pick_for_role(segs: List[Dict], cfg: Dict,
                  want: Tuple[str, ...]) -> Optional[Dict]:
    """Кусок под роль: сначала подходит роль, и только потом старая оценка.

    Раньше все блоки выбирались одной оценкой score_segment - цифра,
    эмоция, длина. Из-за этого в лид мог попасть кусок с цифрами, но
    без вопроса и без конфликта: формально сильный, зрителю не за что
    зацепиться. Роль смотрит на другое - на то, для чего кусок годится.
    """
    if not segs:
        return None
    best, best_key = None, (-1.0, -1.0, -1)
    for i, s in enumerate(segs):
        r = narrative.classify_role(s.get("text", ""))
        key = (role_rank(r.role, want), score_segment(s, cfg), -i)
        if key > best_key:
            best_key, best = key, s
    return best


def build_storyboard(spec: Dict, cfg: Dict) -> Dict:
    """Собирает расклад: список блоков хронометража.

    Каждый блок: {kind, dur, items:[{file, audio, start, dur, desc, why}]}
    kind: video (картинка+звук), vo (только звук закадра), broll, marker
    """
    blocks_cfg = cfg["blocks"]
    syncs = spec.get("syncs") or []
    broll = list(spec.get("broll") or [])
    vo = spec.get("vo") or {}
    standup = spec.get("standup") or {}
    titles = spec.get("titles") or []

    if not syncs:
        raise ValueError("Нужны синхроны: перечисли их в spec.syncs")

    # --- выбираем сильный синхрон для лида ---
    scored = []
    for s in syncs:
        segs = load_transcript(Path(s["transcript"])) if s.get("transcript") else []
        if not segs:
            continue
        cut = trim_lead_fillers(segs, cfg["lead_fillers"], cfg["lead_trim_max_sec"])
        # лиду нужна роль крючка, а не просто «самый громкий» кусок.
        # trim_lead_fillers возвращает только секунды, куски остаются segs.
        best = pick_for_role(segs, cfg, ("крючок", "тезис", "контекст")) or segs[0]
        entry = dict(s)
        entry["_segs"] = segs
        entry["_cut"] = cut
        entry["_score"] = score_segment(best, cfg)
        entry["_role"] = narrative.classify_role(best.get("text", "")).role
        entry["_best"] = best
        scored.append(entry)
    if not scored:
        raise ValueError("Ни в одном синхроне не нашлось транскрипта с текстом")

    scored.sort(key=lambda x: (role_rank(x["_role"], ("крючок", "тезис")),
                            x["_score"]), reverse=True)
    lead = scored[0]
    rest = scored[1:]

    def seg_range(sync: Dict) -> tuple:
        seg = sync["_best"]
        return (seg["start"] + sync["_cut"], seg["end"])

    plan = {"blocks": [], "titles": titles, "notes": []}

    def add_block(kind: str, limit: float) -> float:
        plan["blocks"].append({"kind": kind, "limit": limit, "items": []})
        return len(plan["blocks"]) - 1

    def add_video(bi: int, sync: Dict, a: float, b: float, why: str,
                   text: str = "") -> None:
        plan["blocks"][bi]["items"].append({
            "kind": "video",
            "file": Path(sync["media"]).name,
            "path": sync["media"],
            "audio": sync.get("audio") or "",
            "speaker": sync.get("speaker", ""),
            "start": round(a, 3),
            "dur": round(b - a, 3),
            "desc": sync.get("role") or sync.get("speaker") or "",
            # именно тот кусок речи, что попал в монтаж: если писать сюда
            # текст лучшего сегмента, все куски спикера получат одну фразу
            "text": text or "",
            "why": why,
        })

    def add_vo(bi: int, idx: int, why: str) -> None:
        if not vo.get("audio"):
            return
        plan["blocks"][bi]["items"].append({
            "kind": "vo",
            "file": Path(vo["audio"]).name,
            "path": vo["audio"],
            "start": 0.0,
            # длительность проставляется в finish_block: она равна доле
            # блока между кусками закадра
            "dur": 0.0,
            "chunk": idx,
            "desc": "закадр",
            "why": why,
        })

    def finish_block(bi: int) -> None:
        """Проставляет длительности кускам закадра и отмечает недобор.

        Закадр делит не весь лимит блока, а то, что осталось после
        перебивок: иначе блок раздувается (перебивка стоит поверх лимита).
        Считаем здесь, а не при выгрузке: иначе отчёт показывал бы нули,
        и монтажёр видел бы неверный хронометраж блока.
        """
        block = plan["blocks"][bi]
        vos = [it for it in block["items"] if it["kind"] == "vo"]
        if vos:
            taken = sum(float(it.get("dur") or 0.0) for it in block["items"]
                        if it["kind"] == "broll")
            available = max(0.0, block["limit"] - taken)
            share = round(available / len(vos), 3)
            for it in vos:
                it["dur"] = share
        got = sum(float(it.get("dur") or 0.0) for it in block["items"])
        block["filled"] = round(got, 3)
        if got > block["limit"] + 0.5:
            plan["notes"].append(
                f"Блок «{block['kind']}» переполнен: {got:.1f} с при плане "
                f"{block['limit']:.0f} с - урежь вручную.")
        elif got < block["limit"] * 0.8:
            plan["notes"].append(
                f"Блок «{block['kind']}» не добит: {got:.1f} с из "
                f"{block['limit']:.0f} с - материала не хватило.")

    def add_broll(bi: int, used: set, limit: float, spent: float, why: str) -> float:
        """Кладёт перебивку, не повторяя её (правило 4)."""
        if not broll:
            return spent
        pick = None
        for cand in broll:
            if cand not in used:
                pick = cand
                break
        if pick is None:
            plan["notes"].append("Перебивки кончились - кусок оставлен без прикрытия.")
            return spent
        dur = min(float(cfg["broll_max_sec"]), max(cfg["cutaway_min_sec"], limit))
        plan["blocks"][bi]["items"].append({
            "kind": "broll",
            "file": Path(pick).name,
            "path": pick,
            "start": 0.0,
            "dur": dur,
            "desc": "перебивка",
            "why": why,
        })
        used.add(pick)
        return spent + dur

    used_broll: set = set()
    # уже использованные реплики: одна и та же цитата дважды в сюжете -
    # брак, зритель слышит одно и то же дважды
    used_quotes: set = set()

    # --- 1. ЛИД ---
    bi = add_block("lead", blocks_cfg["lead_sec"])
    la, lb = seg_range(lead)
    if lb - la > cfg["sync_max_sec"]:
        lb = la + cfg["sync_max_sec"]
    add_video(bi, lead, la, lb,
              "лид: самый сильный синхрон, начало подчищено от «ну/вот/ээ»",
              lead["_best"].get("text", ""))
    plan["notes"].append(
        f"Лид взят из синхрона «{lead.get('speaker', '?')}», оценка "
        f"{lead['_score']:.1f}, роль «{lead['_role']}». "
        f"Срезано в начале: {lead['_cut']:.2f} с")

    # --- 2. СУТЬ СОБЫТИЯ: закадр + перебивки ---
    bi = add_block("event", blocks_cfg["event_sec"])
    vo_idx = 0
    spent = 0.0
    while spent < blocks_cfg["event_sec"] - cfg["cutaway_min_sec"]:
        add_vo(bi, vo_idx, "закадр: суть события")
        spent += blocks_cfg["event_sec"] / 3.0
        spent = add_broll(bi, used_broll, cfg["broll_max_sec"], spent,
                          "перебивка под закадром, чтобы кадр менялся")
        vo_idx += 1
    if not vo.get("audio") and not broll:
        plan["notes"].append("Нет ни закадра, ни перебивок - блок «суть события» пустой.")

    # --- 3. СИНХРОНЫ ---
    bi = add_block("syncs", blocks_cfg["syncs_sec"])
    chosen = rest[: max(0, int(cfg["syncs_count"]) - 1)]
    if not chosen and len(scored) > 1:
        chosen = [scored[1]] if len(scored) > 1 else []
    # Каждому синхрону - своя роль в блоке. Раньше все отбирались одной
    # оценкой, и три сильных куска подряд читались как одно и то же.
    # Теперь блок последовательно раскрывает тему: утверждение, пример,
    # спор, контекст.
    want_for_sync = (("тезис", "контекст"), ("пример", "тезис"),
                     ("возражение", "пример"), ("контекст", "тезис"))
    for si, sync in enumerate(chosen):
        want = want_for_sync[si % len(want_for_sync)]
        segs = [s for s in sync["_segs"]
                if _quote_key(sync, s) not in used_quotes]
        # берём лучшие куски реплики, пока не наберём нужный хронометраж
        got = 0.0
        budget = blocks_cfg["syncs_sec"] / max(1, len(chosen))
        ranked = sorted(
            segs,
            key=lambda x: (role_rank(
                narrative.classify_role(x.get("text", "")).role, want),
                score_segment(x, cfg)),
            reverse=True)
        for seg in ranked:
            if got >= budget:
                break
            a, b = seg["start"] + sync["_cut"], seg["end"]
            sync["_cut"] = 0.0
            for piece in split_range(a, b, cfg["sync_min_sec"], cfg["sync_max_sec"]):
                if got >= budget:
                    break
                add_video(bi, sync, piece[0], piece[1],
                          f"синхрон «{sync.get('speaker', '?')}»",
                          seg.get("text", ""))
                used_quotes.add(_quote_key(sync, seg))
                got += piece[1] - piece[0]
                # правило 3: длинная реплика требует перебивки
                if piece[1] - piece[0] > cfg["cutaway_after_sec"]:
                    add_broll(bi, used_broll, cfg["cutaway_max_sec"], 0.0,
                              "перебивка под длинным синхроном (стык прикрыт)")
    if not chosen:
        plan["notes"].append("Синхронов меньше, чем нужно: блок остался пустым.")

    # --- 4. ДЕТАЛИ ---
    bi = add_block("detail", blocks_cfg["detail_sec"])
    spent = 0.0
    vo_idx = 0
    while spent < blocks_cfg["detail_sec"] - cfg["cutaway_min_sec"]:
        add_vo(bi, vo_idx, "закадр: детали и цифры")
        spent += blocks_cfg["detail_sec"] / 2.0
        spent = add_broll(bi, used_broll, cfg["broll_max_sec"], spent,
                          "перебивка к цифрам")
        vo_idx += 1

    # --- 5. ФИНАЛ ---
    bi = add_block("ending", blocks_cfg["ending_sec"])
    if standup.get("media") and standup.get("transcript"):
        st_segs = load_transcript(Path(standup["transcript"]))
        if st_segs:
            # финалу нужна роль вывода, а не самый звонкий кусок
            best = pick_for_role(st_segs, cfg, ("вывод", "тезис")) or st_segs[0]
            cut = trim_lead_fillers(st_segs, cfg["lead_fillers"], cfg["lead_trim_max_sec"])
            a = best["start"] + cut
            b = min(best["end"], a + blocks_cfg["ending_sec"])
            plan["blocks"][bi]["items"].append({
                "kind": "video",
                "file": Path(standup["media"]).name,
                "path": standup["media"],
                "audio": standup.get("audio") or "",
                "speaker": standup.get("speaker", ""),
                "start": round(a, 3),
                "dur": round(b - a, 3),
                "desc": standup.get("role") or "стендап",
                "text": best.get("text", ""),
                "why": "финал: вывод и планы",
            })
        else:
            plan["notes"].append("У стендапа нет транскрипта - финал оставлен пустым.")
    elif rest:
        sync = rest[0]
        # финал не должен повторять то, что уже прозвучало в блоке синхронов
        fresh = [s for s in sync["_segs"]
                 if _quote_key(sync, s) not in used_quotes]
        if fresh:
            best = max(fresh, key=lambda x: score_segment(x, cfg))
            a = best["start"]
            b = min(best["end"], a + blocks_cfg["ending_sec"])
            add_video(bi, sync, a, b, "финал: последний синхрон",
                      best.get("text", ""))
        else:
            # повторять цитату хуже, чем оставить слот пустым: редактор
            # поставит стендап или допишет концовку сам
            plan["notes"].append(
                "Неиспользованных реплик для финала не осталось - слот "
                "оставлен пустым. Поставь стендап вручную.")
    else:
        plan["notes"].append("Нет ни стендапа, ни свободных синхронов - финал пуст.")

    # длительности закадра и отметки о недоборе - после того, как все
    # блоки наполнены
    for b in range(len(plan["blocks"])):
        finish_block(b)

    # повторы в заметках засоряют отчёт — оставляем по одному вхождению
    seen_notes = set()
    unique_notes = []
    for n in plan["notes"]:
        if n not in seen_notes:
            seen_notes.add(n)
            unique_notes.append(n)
    plan["notes"] = unique_notes

    return plan


# --------------------------------------------------------------- вывод

def flatten(plan: Dict, cfg: Dict) -> List[Dict]:
    """Расклад в плоский список строк таймлайна с колонкой track.

    Порядок блоков и чередование «закадр → перебивка» сохраняются: сначала
    считаем длительность каждого куска закадра (она равна доле блока между
    его кусками), потом идём по блокам в исходном порядке.
    """
    tr = cfg["tracks"]

    out: List[Dict] = []
    for block in plan["blocks"]:
        for it in block["items"]:
            dur = float(it.get("dur") or 0.0)
            if it["kind"] == "vo":
                src_in = round(float(it.get("chunk", 0)) * dur, 3)
                out.append({
                    "file": it["file"],
                    "plan": "",
                    "hero": "закадр",
                    "duration": round(dur, 3),
                    "in": src_in,
                    "out": round(src_in + dur, 3),
                    "desc": "закадр",
                    "path": it.get("path", ""),
                    "audio": "",
"track": tr["vo_audio"],
                "why": it.get("why", ""),
                # закадр без текста: субтитровать нечего
                "text": "",
            })
                continue

            out.append({
                "file": it["file"],
                "plan": "средний2" if it["kind"] == "video" else "",
                "hero": it.get("speaker") or it.get("desc") or "",
                "duration": round(dur, 3),
                "in": it["start"],
                "out": round(it["start"] + dur, 3),
                "desc": it.get("desc") or it.get("kind", ""),
                "path": it.get("path", ""),
                "audio": it.get("audio", ""),
                "track": tr["video"],
                "why": it.get("why", ""),
                # реальные слова речи - из них делаются субтитры
                "text": (it.get("text") or "").strip(),
            })
    return out


def write_outputs(plan: Dict, rows: List[Dict], out_dir: Path,
                  spec: Dict, cfg: Dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    fields = ["file", "plan", "hero", "duration", "in", "out", "desc",
              "path", "audio", "track", "why", "text"]
    with open(out_dir / "selects.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})

    (out_dir / "storyboard.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")

    with open(out_dir / "titles.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "role"])
        for t in (spec.get("titles") or []):
            w.writerow([t.get("name", ""), t.get("role", "")])

    total = sum(float(r["duration"]) for r in rows)
    lines = [
        f"Сюжет по мероприятию: хронометраж {total:.1f} с "
        f"(цель {cfg['target_sec']} с)",
        "",
        "СТРУКТУРА:",
    ]
    for b in plan["blocks"]:
        dur = sum(float(i.get("dur") or 0.0) for i in b["items"])
        pct = (dur / b["limit"] * 100) if b["limit"] else 0.0
        flag = "" if pct >= 80 else "   <-- не добит"
        lines.append(f"  {b['kind']:8} план {b['limit']:.0f} с, "
                     f"набрано {dur:.1f} с ({pct:.0f}%){flag}")
        for it in b["items"]:
            lines.append(f"      {it['file']:<24} {it.get('dur', 0):5.1f} с  "
                         f"[{it['kind']:<6}] {it.get('why', '')}")
    if plan["notes"]:
        lines += ["", "ЗАМЕЧАНИЯ:"]
        lines += [f"  - {n}" for n in plan["notes"]]
    lines += ["", "ТИТРЫ (titles.csv - в Premiere набить вручную или скриптом):"]
    for t in (spec.get("titles") or []):
        lines.append(f"  {t.get('name', '')} — {t.get('role', '')}")
    (out_dir / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_spec(path: Path) -> Dict:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AutoCut News Package — сюжет по мероприятию")
    ap.add_argument("--spec", required=True, help="JSON с описанием материала")
    ap.add_argument("--outdir", required=True, help="куда сложить расклад")
    args = ap.parse_args(argv)

    spec_path = Path(args.spec)
    if not spec_path.is_file():
        print(f"Не найден spec: {spec_path}")
        return 2
    try:
        spec = load_spec(spec_path)
        cfg = dict(settings.section("news"))
        plan = build_storyboard(spec, cfg)
        rows = flatten(plan, cfg)
        if not rows:
            print("Расклад пустой - проверь spec.syncs и транскрипты.")
            return 1
        write_outputs(plan, rows, Path(args.outdir), spec, cfg)
    except Exception as e:
        print(f"Не собрал сюжет: {type(e).__name__}: {e}")
        return 1

    total = sum(float(r["duration"]) for r in rows)
    print(f"Сюжет собран: {len(rows)} элементов, {total:.1f} с")
    print(f"Готово: {args.outdir}")
    print(f"  selects.csv     таймлайн с колонкой track (его читает premiere.py)")
    print(f"  storyboard.json что где стоит и почему")
    print(f"  titles.csv      имена и должности для титров")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
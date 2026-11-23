"""Сверка плана движка с эталонной секвенцией из готового проекта.

Эталон - секвенция, которую монтажёр собрал руками (читается из
.prproj через `io/prproj.py`). План - `plan.csv`, который написал
дайджест. Сверяются доли частей, число и длины кусков нарезки,
общий хронометраж. Ничего не пересчитывает и ничего не пишет:
возвращает числа и список расхождений, которые стоит посмотреть.

    python -m engine.compare autocut_out/plan.csv эталон.prproj Дайджест
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Dict, List

import engine  # noqa: F401  (пути для src/)
import prproj

PART_ALIASES = {
    "стендап": "standup", "standup": "standup",
    "интервью": "interview", "interview": "interview",
    "нарезка": "broll", "перебивка": "broll", "broll": "broll",
    "звк": "recorder", "recorder": "recorder",
    "музыка": "music", "music": "music",
}

# допуск по доле части: на столько абсолютных пунктов можно отличаться
SHARE_TOL = 0.08
# допуск по длительности, доля от эталона
DUR_TOL = 0.25


def _f(v, default=0.0) -> float:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return default


def plan_stats(csv_path) -> dict:
    """Сводка по plan.csv в тех же ключах, что у prproj.summary."""
    rows = []
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            # Только видеодорожки: музыка и звук рекордера лежат на A-дорожках,
            # и в долях экрана им места нет — иначе «музыка» занимает 72%
            # ролика и сверка показывает расхождения там, где их нет.
            if (r.get("track") or "").strip().upper().startswith("A"):
                continue
            part = PART_ALIASES.get((r.get("part") or "").strip().lower(), "other")
            dur = _f(r.get("duration"))
            if dur <= 0:
                dur = max(0.0, _f(r.get("out")) - _f(r.get("in")))
            pos = r.get("pos")
            start = _f(pos) if str(pos or "").strip() else None
            rows.append((part, dur, start))
    total = 0.0
    run = 0.0
    for part, dur, start in rows:
        if start is not None:
            total = max(total, start + dur)
        else:
            run += dur
            total = max(total, run)
    shares: Dict[str, float] = {}
    for part, dur, _ in rows:
        shares[part] = shares.get(part, 0.0) + dur
    shares = {k: round(v / total, 3) for k, v in sorted(shares.items())} if total else {}
    b = sorted(d for p, d, _ in rows if p == "broll")
    return {
        "duration_sec": round(total, 1),
        "video_items": len(rows),
        "shares": shares,
        "broll_count": len(b),
        "broll_median_sec": round(b[len(b) // 2], 2) if b else 0.0,
        "broll_min_sec": round(b[0], 2) if b else 0.0,
        "broll_max_sec": round(b[-1], 2) if b else 0.0,
    }


def compare(plan_csv, prproj_path, sequence: str) -> dict:
    """Возвращает {'plan': .., 'reference': .., 'issues': [..]}."""
    ref_seq = prproj.parse(prproj_path).sequence(sequence)
    if ref_seq is None:
        raise ValueError(f"в проекте нет секвенции «{sequence}»")
    ref = prproj.summary(ref_seq)
    plan = plan_stats(plan_csv)
    issues: List[str] = []

    if ref["duration_sec"] and abs(plan["duration_sec"] - ref["duration_sec"]) \
            > DUR_TOL * ref["duration_sec"]:
        issues.append(f"хронометраж {plan['duration_sec']} с, в эталоне "
                      f"{ref['duration_sec']} с")
    for part in ("standup", "interview", "broll"):
        a = plan["shares"].get(part, 0.0)
        b = ref["shares"].get(part, 0.0)
        if abs(a - b) > SHARE_TOL:
            issues.append(f"доля «{part}»: {a:.0%} против {b:.0%} в эталоне")
    if ref["broll_max_sec"] and plan["broll_max_sec"] > ref["broll_max_sec"] * 1.25:
        issues.append(f"самая длинная нарезка {plan['broll_max_sec']} с, "
                      f"в эталоне до {ref['broll_max_sec']} с")
    if ref["broll_min_sec"] and plan["broll_min_sec"] \
            and plan["broll_min_sec"] < ref["broll_min_sec"] * 0.75:
        issues.append(f"есть слишком короткая нарезка {plan['broll_min_sec']} с, "
                      f"в эталоне от {ref['broll_min_sec']} с")
    return {"plan": plan, "reference": ref, "issues": issues}


def report(res: dict) -> str:
    p, r = res["plan"], res["reference"]
    lines = [
        f"Хронометраж: план {p['duration_sec']} с, эталон {r['duration_sec']} с",
        f"Доли: план {p['shares']}",
        f"      эталон {r['shares']}",
        f"Нарезка: план {p['broll_min_sec']}-{p['broll_max_sec']} с "
        f"(медиана {p['broll_median_sec']}), эталон {r['broll_min_sec']}-"
        f"{r['broll_max_sec']} с (медиана {r['broll_median_sec']})",
    ]
    lines += (["Расхождения:"] + [f"  - {x}" for x in res["issues"]]
              if res["issues"] else ["Расхождений сверх допуска нет."])
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("usage: python -m engine.compare plan.csv project.prproj Секвенция")
        sys.exit(2)
    print(report(compare(*sys.argv[1:])))

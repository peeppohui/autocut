# -*- coding: utf-8 -*-
"""Сверка плана с эталоном: доли, длины нарезки, хронометраж."""
import csv
import sys
import tempfile
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
from engine import compare as C

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


def write(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["n", "file", "part", "path", "in", "out", "duration", "pos"])
        for i, (part, dur, pos) in enumerate(rows, 1):
            w.writerow([i, f"{i}.mp4", part, "", 0, dur, dur, pos])


with tempfile.TemporaryDirectory() as td:
    p = _P(td) / "plan.csv"
    # 20 с стендапа, затем 6 кусков нарезки по 2 с
    rows = [("стендап", 20, 0)] + [("нарезка", 2, 20 + 2 * i) for i in range(6)]
    write(p, rows)
    st = C.plan_stats(p)
    ok("хронометраж по pos", st["duration_sec"] == 32.0, str(st))
    ok("доля стендапа", abs(st["shares"]["standup"] - 0.625) < 0.001, str(st["shares"]))
    ok("число и медиана нарезки", st["broll_count"] == 6 and st["broll_median_sec"] == 2.0, str(st))

    # без pos дорожки считаются подряд
    write(p, [("интервью", 10, ""), ("нарезка", 5, "")])
    ok("хронометраж без pos", C.plan_stats(p)["duration_sec"] == 15.0)

    ok("неизвестная часть -> other", "other" in (lambda: (write(p, [("что-то", 4, "")]), C.plan_stats(p))[1])()["shares"])

    # музыка на A-дорожке не должна попадать в доли экрана
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["n", "file", "part", "track", "in", "out", "duration", "pos"])
        w.writerow([1, "a.mp4", "стендап", "V1", 0, 10, 10, 0])
        w.writerow([2, "m.mp3", "музыка", "A3", 0, 10, 10, 0])
    st = C.plan_stats(p)
    ok("музыка с A-дорожки не в долях",
       "music" not in st["shares"] and abs(st["shares"]["standup"] - 1.0) < 1e-6,
       str(st["shares"]))

# issues на подставном эталоне
ref = {"duration_sec": 100.0, "shares": {"standup": 0.2, "interview": 0.25, "broll": 0.55},
       "broll_min_sec": 1.2, "broll_max_sec": 2.6, "broll_median_sec": 1.5}
plan = {"duration_sec": 200.0, "shares": {"standup": 0.5, "broll": 0.5},
        "broll_min_sec": 0.5, "broll_max_sec": 9.0, "broll_median_sec": 5.0}
orig = C.prproj.parse


class _S:
    def __init__(self): pass


C.prproj.parse = lambda path: type("P", (), {"sequence": lambda self, n: object()})()
C.prproj.summary = lambda s: ref
C.plan_stats = lambda p: plan
res = C.compare("x", "y", "z")
iss = " | ".join(res["issues"])
ok("ловит хронометраж", "хронометраж" in iss, iss)
ok("ловит долю стендапа", "standup" in iss, iss)
ok("ловит пропавшее интервью", "interview" in iss, iss)
ok("ловит длинную нарезку", "длинная" in iss, iss)
ok("ловит короткую нарезку", "короткая" in iss, iss)
ok("отчёт печатается", "Расхождения" in C.report(res))

if fail:
    print("ПАДЕНИЕ:", ", ".join(fail))
    sys.exit(1)
print("COMPARE OK")

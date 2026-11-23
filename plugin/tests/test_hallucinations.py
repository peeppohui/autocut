# -*- coding: utf-8 -*-
"""Фразы-галлюцинации Whisper снимаются, настоящая речь остаётся."""
import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src" / "modes"))
import bootstrap  # noqa: F401,F403

import interview as I

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


def mk(text):
    return [I.Word(w, i, i + 0.5, 0.9) for i, w in enumerate(text.split())]


ws = mk("Субтитры сделал DimaTorzok")
n = I.drop_known_hallucinations(ws)
ok("фраза и ник сняты", n == 3 and all(w.drop for w in ws), str(n))

ws = mk("Продолжение следует...")
ok("продолжение следует снято", I.drop_known_hallucinations(ws) == 2)

ws = mk("Сегодня у нас проходило мероприятие в Политехе")
ok("живая речь не тронута",
   I.drop_known_hallucinations(ws) == 0 and not any(w.drop for w in ws))

ws = mk("Субтитры нужно сделать к понедельнику")
ok("похожие слова без точной фразы не трогаются",
   I.drop_known_hallucinations(ws) == 0)

ws = mk("Привет всем Субтитры подогнал Симон спасибо что смотрите")
n = I.drop_known_hallucinations(ws)
ok("после ника речь сохраняется",
   n == 4 and not ws[-1].drop and not ws[-2].drop, f"{n} {[w.drop for w in ws]}")

ws = mk("Субтитры сделал")
ok("повтор вызова не удваивает счётчик",
   I.drop_known_hallucinations(ws) == 2 and I.drop_known_hallucinations(ws) == 0)

if fail:
    print("ПАДЕНИЕ:", ", ".join(fail))
    sys.exit(1)
print("HALLUCINATIONS OK")

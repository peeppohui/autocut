# -*- coding: utf-8 -*-
"""Тест подгонки рыбы под заданный хронометраж.

Главное здесь - то, что НЕЛЬЗЯ делать: выбрасывать зачин и концовку,
и приговаривать кусок за одно попавшееся слово вроде «подождите».
Обе ошибки были в первой версии и ломали интервью насмерть.
"""
import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
import bootstrap  # noqa: F401,F403

import interview
import settings

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


def eq(name, got, want):
    ok(name, got == want, f"{got!r} против {want!r}")


# --- разбор хронометража ---
eq("10:00", interview.parse_duration("10:00"), 600.0)
eq("600", interview.parse_duration("600"), 600.0)
eq("1:02:30", interview.parse_duration("1:02:30"), 3750.0)
eq("0:45", interview.parse_duration("0:45"), 45.0)
eq("пусто значит нет цели", interview.parse_duration(""), 0.0)
eq("мусор не ломает", interview.parse_duration("abc"), 0.0)

# --- куски: индекс -> (t0, t1, спикер, текст, камера) ---
LEAD = "привет! У меня сегодня в гостях Богдан, и мы будем говорить про игры"
TAIL = "Себе, спасибо большое. Всем пока."
BODY = "мы собираем партию и каждый раз забываем правила, поэтому начинаем заново"


def mk(n_body=6, body_sec=12.0):
    out = [(100.0, 106.0, "ведущий", LEAD, "wide")]
    t = 110.0
    for k in range(n_body):
        out.append((t, t + body_sec, "гость", BODY, "wide"))
        t += body_sec + 1.0
    out.append((t, t + 4.0, "гость", TAIL, "wide"))
    return out


total = lambda ch: sum(o - i for i, o, *_ in ch)

ch = mk()
ok("зачин и концовка на месте до подгонки",
   ch[0][3] == LEAD and ch[-1][3] == TAIL)

# --- цель не задана: ничего не трогаем ---
kept, drop, now = interview.fit_to_target(list(ch), 0)
eq("без цели не режем", len(kept), len(ch))
eq("без цели ничего не вырезано", len(drop), 0)

# --- материала меньше цели: тоже не режем ---
kept, drop, now = interview.fit_to_target(list(ch), 10000)
eq("цель больше материала - не режем", len(drop), 0)

# --- подгонка под цель ---
kept, drop, now = interview.fit_to_target(list(ch), 40.0)
ok("укладывается в цель", now <= 40.5, f"{now:.1f}с")
ok("зачин уцелел", kept[0][3] == LEAD, kept[0][3][:40])
ok("концовка уцелела", kept[-1][3] == TAIL, kept[-1][3][:40])
ok("что-то вырезано", 0 < len(drop) < len(ch), str(len(drop)))

# --- одно «подождите» в длинной фразе не приговаривает кусок ---
lead_with_word = ("Я просто говорю, подождите, стойте, всем привет. "
                  "Сегодня у нас тема, и мы её обсуждаем подробно")
eq("фраза с одним техническим словом не техничная",
   interview.chunk_score(0.0, 6.0, lead_with_word) < 1000, True)
eq("чистая перебивка - техничная",
   interview.chunk_score(0.0, 2.0, "подождите стоп подождите"),
   1000)
eq("разговор про петли (техслово) не приговор",
   interview.chunk_score(0.0, 6.0, "у меня всегда петли и я их теряю") < 1000,
   True)

# --- режем слитные блоки, а не куски по одному ---
ch2 = mk(n_body=10)
kept2, drop2, now2 = interview.fit_to_target(list(ch2), 70.0)
dropped_idx = [k for k, c in enumerate(ch2) if c in drop2]
runs = []
for k in dropped_idx:
    if runs and k == runs[-1][-1] + 1:
        runs[-1].append(k)
    else:
        runs.append([k])
ok("вырезано слитными блоками", len(runs) <= 3, f"блоков: {len(runs)}")

# --- если не хватает - честно говорим, а не врубаем зачин ---
tiny = [(100.0, 106.0, "ведущий", LEAD, "wide"),
        (110.0, 130.0, "гость", BODY, "wide"),
        (140.0, 144.0, "гость", TAIL, "wide")]
kept3, drop3, now3 = interview.fit_to_target(list(tiny), 5.0)
ok("зачин не режется даже в крайнем случае",
   any(c[3] == LEAD for c in kept3), str([c[3][:20] for c in kept3]))
ok("концовка не режется даже в крайнем случае",
   any(c[3] == TAIL for c in kept3))
ok("и честно виден перебор", now3 > 5.5, f"{now3:.1f}с")

print()
if fail:
    print(f"TARGET FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("TARGET OK")

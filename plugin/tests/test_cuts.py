# -*- coding: utf-8 -*-
"""Правила раскладки камер: доля основной и длина кадра.

Проверяется чистая функция balance_cameras, без видео и без моделей:
правила должны считаться руками и проверяться, а не «посмотреть
в Premiere и почувствовать».

Смысл правил, который проверяется:
  1. камера следует за говорящим; монолог гостя идёт на гостевой камере;
  2. смена говорящего режется, но не чаще, чем раз в shot_min;
  3. на ОБЩЕМ плане, который задержался дольше opt_max, программа
     уходит на вторую камеру для разнообразия - но только если у той
     ещё осталась часть эфира (cam_a_share) и второй кадр не выйдет
     короче opt_min;
  4. общий план дольше shot_max не держится никогда, даже через долю.
"""
import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
import bootstrap  # noqa: F401,F403

import interview

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


def chunks(pairs):
    """pairs: [(in, out, speaker)] -> строки для balance_cameras."""
    return [(i, o, sp, "речь", "gcam" if sp == "гость" else "wide")
            for i, o, sp in pairs]


def cams(rows):
    return [r[4] for r in rows]


def share_b(rows):
    """Доля экранного времени второй камеры."""
    total = sum(o - i for i, o, _s, _t, _w in rows)
    b = sum(o - i for i, o, _s, _t, w in rows if w != "wide")
    return b / total if total else 0.0


def run(rows, **kw):
    opts = dict(share_a=0.7, shot_min=2.0, opt_min=4.0, opt_max=7.0,
                shot_max=12.0)
    opts.update(kw)
    return interview.balance_cameras(rows, **opts)


# --- 1. камера следует за говорящим ---
guest_mono = chunks([(0, 10, "гость"), (10, 20, "гость"),
                     (20, 30, "гость"), (30, 40, "гость")])
ok("монолог гостя идёт на гостевой камере",
   set(cams(run(guest_mono))) == {"gcam"}, str(cams(run(guest_mono))))
ok("и доля основной его не ломает",
   set(cams(run(guest_mono, share_a=1.0))) == {"gcam"},
   str(cams(run(guest_mono, share_a=1.0))))

# --- 2. пол против дребезга ---
dribble = chunks([(0, 0.5, "ведущий"), (0.5, 1.0, "гость"),
                  (1.0, 1.5, "ведущий"), (1.5, 2.0, "гость"),
                  (2.0, 2.5, "ведущий")])
ok("короткие куски не дают дребезга",
   set(cams(run(dribble, shot_min=2.0))) == {"wide"},
   str(cams(run(dribble, shot_min=2.0))))
ok("с полом поменьше переключение происходит",
   len(set(cams(run(dribble, shot_min=0.0)))) > 1,
   str(cams(run(dribble, shot_min=0.0))))

# --- 3. разнообразие на общем плане ---
host_mono = chunks([(0, 10, "ведущий"), (10, 20, "ведущий"),
                    (20, 30, "ведущий"), (30, 40, "ведущий")])
mix = run(host_mono, opt_max=5.0, opt_min=2.0, shot_max=30.0, share_a=0.6)
ok("затянувшийся общий план пускает вторую камеру",
   "gcam" in cams(mix), str(cams(mix)))
ok("вторая камера не съедает больше своей доли",
   share_b(mix) <= 0.41, str(share_b(mix)))
ok("и доля действительно ограничивает", share_b(mix) > 0.0, str(share_b(mix)))

none_mix = run(host_mono, opt_max=5.0, opt_min=2.0, shot_max=30.0, share_a=1.0)
ok("при доле 1.0 вторая камера на разнообразие не берётся",
   "gcam" not in cams(none_mix), str(cams(none_mix)))

# --- 4. потолок ---
ceil = run(host_mono, share_a=1.0, shot_min=0.0, opt_max=7.0, shot_max=12.0)
ok("общий план дольше потолка не держится", "gcam" in cams(ceil),
   str(cams(ceil)))
ok("потолок работает даже при доле 1.0", share_b(ceil) > 0.0, str(share_b(ceil)))

# --- 5. opt_min не даёт дрожать ---
short = chunks([(0, 1, "ведущий"), (1, 2, "ведущий"),
                (2, 3, "ведущий"), (3, 4, "ведущий")])
tight = run(short, share_a=0.6, shot_min=0.0, opt_min=3.0, opt_max=0.5,
            shot_max=30.0)
loose = run(short, share_a=0.6, shot_min=0.0, opt_min=0.2, opt_max=0.5,
            shot_max=30.0)
ok("opt_min держит второй кадр и не даёт дрожать",
   cams(tight) != cams(loose), f"{cams(tight)} против {cams(loose)}")
ok("короткий opt_min переключает чаще",
   len(set(cams(tight))) <= len(set(cams(loose))), f"{cams(tight)}")

# --- каждый ползунок влияет на картинку ---
# Сравниваем при ОСТАЛЬНЫХ ползунках, зафиксированных одинаково: иначе
# выигрывает не тот, кого проверяем, а тот, у кого значение больше.
# shot_min виден только на коротких кусках, opt_min - при снятом полу.
cases = (
    ("shot_min", dribble, dict(shot_min=2.0), dict(shot_min=0.0)),
    ("opt_min", short,
     dict(shot_min=0.0, opt_min=4.0, opt_max=0.5, shot_max=30.0,
          share_a=0.6),
     dict(shot_min=0.0, opt_min=0.2, opt_max=0.5, shot_max=30.0,
          share_a=0.6)),
    ("opt_max", host_mono, dict(opt_max=7.0), dict(opt_max=100.0)),
    ("shot_max", host_mono, dict(shot_max=12.0), dict(shot_max=3.0)),
    ("cam_a_share", host_mono, dict(share_a=0.7), dict(share_a=0.2)),
)
for label, rows, before_kw, after_kw in cases:
    before, after = run(rows, **before_kw), run(rows, **after_kw)
    ok(f"ползунок {label} влияет на раскладку", cams(before) != cams(after),
       f"{after_kw}: {cams(before)} против {cams(after)}")

# --- прочее ---
ok("пустой список не падает", interview.balance_cameras([]) == [])
one = run(chunks([(0, 5, "гость")]))
ok("строка сохраняет пять полей", len(one[0]) == 5, str(one[0][:1]))
ok("таймкод и текст не пострадали",
   one[0][0] == 0 and one[0][1] == 5 and one[0][2] == "гость", str(one[0]))
ok("число кусков не меняется", len(run(host_mono)) == len(host_mono),
   str(len(run(host_mono))))

print()
if fail:
    print(f"FIELDS FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("FIELDS OK")
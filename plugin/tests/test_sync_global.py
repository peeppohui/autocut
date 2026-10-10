# -*- coding: utf-8 -*-
"""Тест глобального согласования дорожек (метод без хлопка).

Задача: несколько дорожек стартовали в разное время, общего сигнала
хватает только на попарное сравнение. Нужно получить один согласованный
набор сдвигов и проверить, что он сходится.
"""
import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
import bootstrap  # noqa: F401,F403

import numpy as np
from sync import measure_pair, solve_global_offsets

RATE = 20


def make_env(dur=120.0, seed=1, n_events=40):
    """Огибающая: тишина + редкие громкие события (общий сигнал для пар)."""
    rng = np.random.default_rng(seed)
    env = rng.normal(0.0005, 0.0002, int(dur * RATE))
    for s in rng.uniform(2, dur - 2, n_events):
        i = int(s * RATE)
        L = int(RATE * 0.4)
        env[i:i + L] += rng.uniform(0.4, 1.0)
    return np.maximum(env, 0.0)


def shift(env, start):
    """Файл со стартом 'start' секунд: содержимое base(t + start).

    start < 0 — файл начался раньше, поэтому его содержимое идёт
    с более ранних моментов; start > 0 — позже, в начале тишина.
    """
    n = int(round(start * RATE))
    out = np.zeros_like(env)
    if n >= 0:
        if n < len(env):
            out[n:] = env[:len(env) - n]
    else:
        k = -n
        if k < len(env):
            out[:len(env) - k] = env[k:]
    return out


def run_case(name, truth):
    """truth: {имя: момент начала записи, секунды}. Отрицательный = раньше."""
    base = make_env(dur=400.0, seed=1)
    envs = {k: shift(base, -v) for k, v in truth.items()}
    names = list(truth)

    pairs = {}
    for i in range(len(names)):
        for j in range(len(names)):
            if i == j:
                continue
            # окно должно перекрывать любой сдвиг из задания
            lag, contrast, peak, second, usable = measure_pair(
                envs[names[i]], envs[names[j]], RATE, 200.0)
            assert usable and contrast > 2.0, \
                f"{names[i]}<->{names[j]}: unusable peak {contrast:.2f}"
            pairs[(names[i], names[j])] = lag

    offsets, residual = solve_global_offsets(pairs, names, "ref")

    ok = residual < 1.0
    print(f"--- {name} ---")
    print(f"  разброс между парами: {residual:.3f} с")
    for n in names:
        got = offsets[n] - offsets["ref"]
        want = truth[n] - truth["ref"]
        mark = "OK " if abs(got - want) < 0.5 else "FAIL"
        print(f"  {mark} {n:11} начало {got:+7.2f} с, ожидалось {want:+7.2f} с")
        if mark == "FAIL":
            ok = False
    return ok


def test_inconsistent():
    """Совершенно несвязанные дорожки: решения быть не должно."""
    rng = np.random.default_rng(99)
    a = np.abs(rng.normal(0.01, 0.004, 2000))
    b = np.abs(rng.normal(0.01, 0.004, 2000))
    pairs = {("ref", "cam"): 3.0, ("ref", "mic_guest"): 100.0,
             ("cam", "mic_guest"): 5.0}
    offsets, residual = solve_global_offsets(
        pairs, ["ref", "cam", "mic_guest"], "ref")
    print("--- несвязанные дорожки ---")
    print(f"  разброс {residual:.2f} с (ожидаем большой) -> "
          f"{'отвергнуто верно' if residual > 1.5 else 'ПРОВАЛ'}")
    return residual > 1.5


def test_window_too_big():
    """Окно шире сигнала: метод обязан честно сказать, что не годен.

    Раньше здесь возвращались нули, и все нули идеально сходились —
    на выходе получался выдуманный сдвиг 0.00 с.
    """
    env = make_env(dur=20.0, seed=5)
    lag, contrast, peak, second, usable = measure_pair(env, env, RATE, 600.0)
    print("--- окно шире сигнала ---")
    print(f"  пригодность {usable}, контраст {contrast:.2f} "
          f"-> {'отвергнуто верно' if not usable or contrast < 2.0 else 'ПРОВАЛ'}")
    return (not usable) or contrast < 2.0


print("=== глобальное согласование ===")
ok = True
ok &= run_case("камеры вместе, микрофоны раньше",
              {"ref": 0.0, "cam": 0.0, "mic_host": -67.0, "mic_guest": -121.0})
ok &= run_case("все с разными сдвигами",
              {"ref": 0.0, "cam": 2.5, "mic_host": -12.0, "mic_guest": -45.5})
ok &= test_window_too_big()
ok &= test_inconsistent()

print()
print("GLOBAL-SYNC", "OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
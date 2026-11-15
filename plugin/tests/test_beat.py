# -*- coding: utf-8 -*-
"""Детектор битов: темп, сетка и привязка монтажа."""
import sys
import tempfile
import wave
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
import bootstrap  # noqa: F401  — регистрирует все модульные папки в sys.path
import numpy as np
from ml import beat

SR = beat.RATE


def clicks(dur=16.0, bpm=120.0, offset=0.0, amp=0.8):
    """Щелчки с заданным темпом: удар = короткий тон с ханнингом."""
    n = int(dur * SR)
    x = np.zeros(n, dtype=np.float32)
    step = 60.0 / bpm
    t = offset
    while t < dur - 0.05:
        i = int(t * SR)
        L = int(0.03 * SR)
        x[i:i + L] += (np.sin(2 * np.pi * 1800 * np.arange(L) / SR).astype(np.float32)
                       * np.hanning(L) * amp)
        t += step
    return x


def write_wav(path, x, sr=SR):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def ok(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else ": " + str(extra)))
    return bool(cond)


ok_all = True

# 1. Темп 120 BPM: удар каждые 0.5 с.
g = beat.grid(x=clicks(bpm=120.0))
ok_all &= ok("темп 120 BPM узнан", g and abs(g["bpm"] - 120.0) < 4.0, g.get("bpm"))
ok_all &= ok("сетка непустая", bool(g and len(g["times"]) > 20), len(g.get("times", [])))
step = 0.5 / 2.0
diffs = [round(g["times"][i + 1] - g["times"][i], 4) for i in range(min(20, len(g["times"]) - 1))]
ok_all &= ok("шаг сетки = полуда beat", all(abs(d - step) < 0.01 for d in diffs), diffs[:6])
ok_all &= ok("первый удар в начале", g["times"] and abs(g["times"][0]) < 0.1, g["times"][:3])

# 2. Темп 90 BPM: удар каждые 0.667 с.
g90 = beat.grid(x=clicks(bpm=90.0))
ok_all &= ok("темп 90 BPM узнан", g90 and abs(g90["bpm"] - 90.0) < 4.0, g90.get("bpm"))

# 3. Тишина и шум: ритма нет — сетка пустая, чтобы монтаж не тянуло в никуда.
g_sil = beat.grid(x=np.zeros(SR * 4, dtype=np.float32))
ok_all &= ok("тишина — ритма нет", g_sil == {}, g_sil)
rng = np.random.default_rng(3)
g_noise = beat.grid(x=rng.normal(0, 0.05, SR * 8).astype(np.float32))
ok_all &= ok("шум — ритма нет", g_noise == {}, g_noise)

# 4. WAV на диске читается так же, как массив.
with tempfile.TemporaryDirectory() as td:
    w = _P(td) / "clicks.wav"
    write_wav(w, clicks(bpm=120.0))
    gw = beat.grid(path=w)
    ok_all &= ok("wav на диске читается", gw and abs(gw["bpm"] - 120.0) < 4.0, gw.get("bpm"))

# 5. Привязка: монтажная точка встаёт на ближайшую долю.
times = g["times"]
snapped = beat.snap(0.13, times)
ok_all &= ok("точка привязалась к доле сетки", snapped in times, snapped)
ok_all &= ok("точка не осталась на месте", abs(snapped - 0.13) > 0.02, snapped)
ok_all &= ok("точка вне сетки не тянется силой",
             beat.snap(3.07, times) == 3.07 or beat.snap(3.07, times) in times,
             beat.snap(3.07, times))

print("BEAT OK" if ok_all else "BEAT FAIL")
raise SystemExit(0 if ok_all else 1)

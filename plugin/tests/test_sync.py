import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
import bootstrap  # noqa: F401  — регистрирует все модульные папки в sys.path
import numpy as np
from sync import find_offset, envelope

SR = 16000
rng = np.random.default_rng(7)


def ev_sparse(dur=20.0):
    n = int(dur * SR)
    x = rng.normal(0, 0.02, n).astype(np.float32)
    for s in (2.0, 5.5, 9.0, 13.0, 16.5):
        i = int(s * SR)
        x[i:i + 8000] += np.sin(2 * np.pi * 220 * np.arange(8000) / SR).astype(np.float32) * 0.5
    return x


def ev_dense(dur=300.0):
    n = int(dur * SR)
    x = rng.normal(0, 0.03, n).astype(np.float32)
    t = 5.0
    while t < dur - 8:  # аритмично: паузы, длины, частоты — всё случайно
        i = int(t * SR)
        L = min(int(rng.uniform(1.5, 4.0) * SR), n - i)
        f = rng.uniform(120, 450)
        x[i:i + L] += (np.sin(2 * np.pi * f * np.arange(L) / SR)
                       * np.hanning(L) * 0.6).astype(np.float32)
        t += rng.uniform(4.0, 11.0)
    return x


def check(name, ref, other, want, tol=0.5):
    re_, rate = envelope(ref)
    oe_, _ = envelope(other)
    off, q, co = find_offset(re_, oe_, rate)
    ok = abs(off - want) < tol
    print(f"{name}: offset={off:+.2f} (want {want:+.2f}) q={q:.2f} co={co:.1f} {'OK' if ok else 'FAIL'}")
    return ok


ok = True
# 1. разреженное + умеренный преролл 43с (шуршание тише речи)
ref = ev_sparse()
pre = np.concatenate([rng.normal(0, 0.15, 40 * SR).astype(np.float32),
                      np.zeros(3 * SR, dtype=np.float32), ref])
ok &= check("sparse+preroll", ref, pre, -43.0, 1.0)
# 2. плотное 5 мин + преролл 43с
ref5 = ev_dense()
pre5 = np.concatenate([rng.normal(0, 0.4, 40 * SR).astype(np.float32),
                       np.zeros(3 * SR, dtype=np.float32), ref5])
ok &= check("dense5min+preroll", ref5, pre5, -43.0, 1.0)
# 3. обрезанное начало +4с (старт позже)
ok &= check("truncated", ref, ref[4 * SR:], +4.0, 0.3)
# 4. чистый сдвиг без мусора
ok &= check("clean", ref, np.concatenate([np.zeros(int(2.5 * SR), dtype=np.float32), ref])[:-int(2.5 * SR)], -2.5, 0.3)
print("SYNC-ALL", "OK" if ok else "FAIL")
sys.exit(0 if ok else 1)

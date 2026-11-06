# -*- coding: utf-8 -*-
"""Сопоставление звука камеры и рекордера по словам.

Проверяется без whisper: слова и время подставляются готовыми. Главное -
сдвиг, качество и то, что второе окно не съезжает, когда в речи есть
короткие слова, которые отбрасываются при нормировании.
"""
import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src" / "ml"))
import bootstrap  # noqa: F401,F403

import recmatch as R

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


def words(text, start=0.0, step=1.0):
    """[(слово, начало, конец)] с ровным шагом."""
    out = []
    t = start
    for w in text.split():
        out.append((w, t, t + step))
        t += step
    return out


def long_words(n, prefix="слово"):
    return " ".join(f"{prefix}{i:02d}" for i in range(n))


# --- нормализация ---
ok("ё и пунктуация нормализуются", R.norm("Ёлка!") == "елка", R.norm("Ёлка!"))
ok("короткие слова не идут в сопоставление",
   [w for w, _t in R.key_words(words("в и не это очень важно"))]
   == ["это", "очень", "важно"], "")

# --- точный сдвиг ---
cam = words(long_words(16), 0.0)
rec = words(long_words(16), 100.0)
off, q = R.match(cam, rec)
ok("сдвиг +100 находится", off is not None and abs(off - 100.0) < 1e-9, str(off))
ok("полное совпадение даёт качество 1.0", abs(q - 1.0) < 1e-9, str(q))

# --- короткие слова в камере не должны съедать второе окно ---
cam_short = words("в и не на " * 2 + long_words(16), 0.0)
off2, q2 = R.match(cam_short, rec)
ok("второе окно камеры не съезжает из-за коротких слов",
   off2 is not None and abs(off2 - 92.0) < 1e-9 and abs(q2 - 1.0) < 1e-9,
   f"offset={off2} quality={q2}")

# --- короткие слова в рекордере не должны съедать второе окно ---
rec_short = words("в и не на " + long_words(16), 10.0)
off3, q3 = R.match(cam, rec_short)
ok("второе окно рекордера не съезжает из-за коротких слов",
   off3 is not None and abs(off3 - 14.0) < 1e-9 and abs(q3 - 1.0) < 1e-9,
   f"offset={off3} quality={q3}")

# --- одно слово не закрывает два ---
ok("одно слово рекордера не закрывает два слова камеры",
   R._score([("это", 0.0), ("это", 1.0), ("это", 2.0)],
            [("это", 5.0)], 0) == 1, "")

# --- случайного совпадения нет ---
other = words("совсем другие слова здесь", 50.0)
off4, q4 = R.match(cam, other)
ok("без общих слов совпадения нет", off4 is None and q4 == 0.0,
   f"offset={off4} quality={q4}")

# --- порог качества ---
half = words(long_words(8) + " " + long_words(8, "другое"), 0.0)
off5, q5 = R.match(cam, half)
ok("половина второго окна даёт качество 0.5",
   off5 is not None and abs(q5 - 0.5) < 1e-9, str(q5))

index = {"REC00001": rec}
name, off6, q6 = R.find(cam, index)
ok("find выбирает файл и сдвиг",
   name == "REC00001" and abs(off6 - 100.0) < 1e-9 and q6 > 0.99,
   f"{name} {off6} {q6}")
ok("find отсекает по порогу",
   R.find(cam, index, min_quality=1.01) == (None, None, 0.0), "")
ok("find не падает на пустом индексе",
   R.find(cam, {}) == (None, None, 0.0), "")

print()
if fail:
    print(f"RECMATCH FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("RECMATCH OK")

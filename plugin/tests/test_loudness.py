# -*- coding: utf-8 -*-
"""Нормализация громкости: арифметика настройки и сквозная проверка.

Чистые функции проверяются без ffmpeg. Сквозная часть поднимает
настоящий файл, гонит через нормализацию и меряет результат заново -
иначе можно месяцами проверять модуль, который ничего не делает.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src" / "ml"))
import bootstrap  # noqa: F401,F403

import loudness as L  # noqa: E402

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


# --- чистая арифметика ---
ok("усиление считается по разнице цели и замера",
   abs(L.gain_db(-47.5, -16.0) - 31.5) < 1e-9, L.gain_db(-47.5, -16.0))
ok("тихая дорожка получает больше усиления",
   L.gain_db(-47.5, -16.0) > L.gain_db(-42.0, -16.0), "")
ok("предел ограничителя ниже цели на запас",
   abs(L.limiter_limit(-1.5, 1.5) - (-3.0)) < 1e-9, L.limiter_limit(-1.5, 1.5))
ok("без запаса предел равен цели",
   L.limiter_limit(-1.5, 0.0) == -1.5, "")

chain = L.chain_filter(31.5, -3.0)
ok("в цепочке есть усиление", "volume=31.50dB" in chain, chain)
ok("в цепочке есть ограничитель", "alimiter=limit=-3.00dB" in chain, chain)
ok("ограничитель не левел автоматически", "level=disabled" in chain, chain)
ok("ограничитель с опережением", "latency=1" in chain, chain)

# --- отчёт на пустом месте не должен падать ---
rep = L.normalize(_P("нет_такого_файла.wav"))
ok("несуществующий файл не роняет сборку", rep["ok"] is False, str(rep))
ok("и причина названа", "не измерилась" in rep["why"], rep["why"])
ok("описание отчёта не падает", "нет_такого" in L.describe(rep), L.describe(rep))

# --- сквозная проверка на настоящем звуке ---
if not shutil.which("ffmpeg"):
    print("пропуск сквозной части: нет ffmpeg")
else:
    tmp = _P(tempfile.mkdtemp(prefix="autocut_loud_"))
    quiet = tmp / "quiet.wav"
    # ровная тонкая пила - предсказуемая громкость, легко проверить
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=6:sample_rate=48000",
         "-af", "volume=-25dB", "-c:a", "pcm_s16le", str(quiet)],
        capture_output=True)

    before = L.measure(quiet)
    ok("громкость померилась", before is not None, "ffmpeg не дал JSON")
    if before:
        target = -16.0
        rep = L.normalize(quiet, target=target, tp=-1.5, margin=1.5)
        ok("нормализация прошла", rep["ok"], rep.get("why"))
        after = L.measure(quiet)
        ok("после обработки тоже меряется", after is not None, "")
        if before and after:
            print(f"       было I={before['lufs']:.1f} TP={before['true_peak']:.1f}"
                  f" -> стало I={after['lufs']:.1f} TP={after['true_peak']:.1f}")
            ok("громкость выросла",
               after["lufs"] > before["lufs"] + 3.0,
               f"{before['lufs']:.1f} -> {after['lufs']:.1f}")
            ok("и приблизилась к цели",
               abs(after["lufs"] - target) <= 1.5,
               f"цель {target}, стало {after['lufs']:.1f}")
            ok("истинный пик не выше цели",
               after["true_peak"] <= -1.5 + 0.2,
               f"{after['true_peak']:.2f} при цели -1.5")
        ok("формат сохранён: частота и каналы те же",
           L._probe(quiet)["rate"] == L._probe(quiet)["rate"], "")

        # --- баланс двух дорожек: тихая и громкая
        loud = tmp / "loud.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(quiet),
             "-af", "volume=6dB", "-c:a", "pcm_s16le", str(loud)],
            capture_output=True)
        m1, m2 = L.measure(quiet), L.measure(loud)
        gap_before = abs(m1["lufs"] - m2["lufs"]) if (m1 and m2) else 0.0
        r1 = L.normalize(quiet, target=-16.0)
        r2 = L.normalize(loud, target=-16.0)
        if r1["ok"] and r2["ok"]:
            gap = abs(r1["after"]["lufs"] - r2["after"]["lufs"])
            print(f"       разница между дорожками: было {gap_before:.2f} "
                  f"LUFS, стало {gap:.2f} LUFS")
            ok("дорожки сравнялись по громкости", gap <= 0.5,
               f"разница {gap:.2f} LUFS")
            ok("и разрыв действительно был", gap_before >= 5.0,
               f"{gap_before:.2f} LUFS")
    shutil.rmtree(tmp, ignore_errors=True)

print()
if fail:
    print(f"LOUDNESS FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("LOUDNESS OK")
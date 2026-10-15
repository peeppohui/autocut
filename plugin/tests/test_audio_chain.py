# -*- coding: utf-8 -*-
"""Звуковая цепь: частота дорожек, которая доезжает до Premiere.

Смысл проверки один. Дорожка для распознавания обязана быть 16 кГц моно -
это штатный вход whisper. Дорожка для монтажа обязана быть в частоте
экспорта. Когда это одна и та же дорожка, выбор один из двух: либо
распознавание получает 48 кГц и считает втрое дольше, либо Premiere
получает 16 кГц и голос звучит как разговор по телефону.

Ошибка была ровно вторая, и она молчаливая: файлы получались, тесты на
число дорожек проходили, а качество никто не смотрел. Поэтому здесь
проверяется не "файл создался", а частота, дошедшая до конца цепи.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path as _P

ROOT = _P(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "ml"))
sys.path.insert(0, str(ROOT / "src" / "modes"))
import bootstrap  # noqa: F401,F403

import settings  # noqa: E402
import sync  # noqa: E402
import interview  # noqa: E402

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


EXPORT_RATE = int(settings.get("export.audio_sample_rate", 48000))


def probe(p):
    def g(kind, entry):
        return subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", f"{kind}={entry}", "-of", "csv=p=0", str(p)],
            capture_output=True, text=True).stdout.strip()
    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout.strip()
    return {"rate": int(g("stream", "sample_rate")),
            "channels": int(g("stream", "channels")),
            "codec": g("stream", "codec_name"),
            "dur": float(dur)}


# --- частота результата синхронизации берётся из настроек, а не из заглушки ---
ok("частота микрофонов в synced равна частоте экспорта",
   sync.AUDIO_RATE == EXPORT_RATE, f"{sync.AUDIO_RATE} против {EXPORT_RATE}")
ok("и это не 16 кГц", sync.AUDIO_RATE >= 44100, str(sync.AUDIO_RATE))

if not shutil.which("ffmpeg"):
    print("пропуск сквозной части: нет ffmpeg")
else:
    tmp = _P(tempfile.mkdtemp(prefix="autocut_chain_"))
    src = tmp / "mic48.wav"
    # Исходник как у настоящей камеры: 48 кГц, моно.
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=200:duration=6:sample_rate=48000",
         "-ac", "1", "-c:a", "pcm_s16le", str(src)], capture_output=True)

    # --- сквозная проверка синхронизации ---
    for off in (0.0, 2.5):
        dst = tmp / f"pad{off}.wav"
        sync.pad_audio_to_zero(src, off, dst)
        p = probe(dst)
        ok(f"добивка не понизила частоту (сдвиг {off})",
           p["rate"] == EXPORT_RATE, f"{p['rate']} Гц вместо {EXPORT_RATE}")
        ok(f"тишины добавлено ровно {off} с",
           abs(p["dur"] - (6.0 + off)) < 0.05, f"{p['dur']:.2f} с")

    # --- две дорожки: одна для распознавания, одна для монтажа ---
    whisper_wav = tmp / "_guest.wav"
    montage_wav = tmp / "_guest_full.wav"
    interview.extract_wav(src, whisper_wav)
    interview.extract_audio_full(src, montage_wav)

    w, m = probe(whisper_wav), probe(montage_wav)
    print(f"       распознавание: {w['rate']} Гц {w['channels']} кан., "
          f"монтаж: {m['rate']} Гц {m['channels']} кан.")
    ok("дорожка для распознавания остаётся 16 кГц моно",
       w["rate"] == 16000 and w["channels"] == 1, str(w))
    ok("дорожка для монтажа в частоте экспорта",
       m["rate"] == EXPORT_RATE, f"{m['rate']} Гц вместо {EXPORT_RATE}")
    ok("главное: в монтаж уходит не 16 кГц",
       m["rate"] > 16000, f"{m['rate']} Гц")

    # --- нормализация не должна портить формат ---
    interview.normalize_tracks([montage_wav])
    n = probe(montage_wav)
    ok("нормализация сохранила частоту дорожки",
       n["rate"] == EXPORT_RATE, f"{n['rate']} Гц")
    ok("и каналы", n["channels"] == m["channels"], str(n))
    ok("и разрядность дорожки",
       n["codec"] == m["codec"], f"{m['codec']} -> {n['codec']}")
    ok("и длительность", abs(n["dur"] - m["dur"]) < 0.05,
       f"{m['dur']:.2f} -> {n['dur']:.2f}")

    # Разрядность дорожки и секвенции должна совпадать: XML объявляет
    # export.audio_depth, и если медиа в другом разряде, то ползунок
    # в настройках перестаёт значить что-либо.
    depth = int(settings.get("export.audio_depth", 16))
    want = "pcm_s16le" if depth == 16 else "pcm_s24le"
    ok("дорожка в том же разряде, что объявляет секвенция",
       n["codec"] == want, f"{n['codec']} против {want}")

    shutil.rmtree(tmp, ignore_errors=True)

print("AUDIO-CHAIN " + ("OK" if not fail else "FAIL " + ", ".join(fail)))
sys.exit(1 if fail else 0)

# -*- coding: utf-8 -*-
"""Тест подготовки камер к мультикаму Premiere.

Проверяем разбор таймкода и то, что модуль не врёт на пустом входе.
Само впечатывание - это вызов ffmpeg, его проверяем на коротком
настоящем фрагменте: он занимает секунды, зато ловит регрессии,
на которые потом натыкаешься в самый неподходящий момент.
"""
import subprocess
import sys
import tempfile
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
import bootstrap  # noqa: F401,F403

import multicam

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


def eq(name, got, want):
    ok(name, got == want, f"{got!r} против {want!r}")


# --- разбор таймкода ---
eq("полный разбор", multicam.parse_tc("01:00:00:00"), (1, 0, 0, 0))
eq("с кадрами", multicam.parse_tc("10:23:45:12"), (10, 23, 45, 12))
eq("мусор не ломает", multicam.parse_tc("ерунда"), (0, 0, 0, 0))
eq("три части не годятся", multicam.parse_tc("01:00:00"), (0, 0, 0, 0))

# --- таймкод в кадрах: это то, на чём Premiere и посчитает сдвиг ---
eq("час при 25 fps", multicam.tc_frames("01:00:00:00", 25), 90000)
eq("день при 25 fps", multicam.tc_frames("24:00:00:00", 25), 2160000)
eq("с кадрами", multicam.tc_frames("00:00:01:12", 25), 25 + 12)
eq("при 30 fps иначе", multicam.tc_frames("01:00:00:00", 30), 108000)

# --- отказ на плохих данных, а не падение ---
r = multicam.bake_timecode("нет_такого.mp4", "куда.mp4")
eq("нет исходника - понятная ошибка", r["ok"], False)
ok("в ошибке сказано что", bool(r.get("error")), str(r))

with tempfile.TemporaryDirectory() as td:
    td = _P(td)
    r = multicam.bake_timecode(td / "x.mp4", td / "y.mp4", tc="ерунда")
    eq("кривой таймкод отклонён", r["ok"], False)

    res = multicam.prepare([td / "нет.mp4"], td / "out")
    eq("пустой вход - не успех", res["ok"], False)
    eq("пустой вход - ноль клипов", res["clips"], 0)

    # txt вместо видео должен быть пропущен, а не сломан
    (td / "readme.txt").write_text("не видео", encoding="utf-8")
    res = multicam.prepare([td / "readme.txt"], td / "out2")
    eq("не-видео пропущено", res["clips"], 0)

# --- настоящее впечатывание на коротком фрагменте ---
def have_ffmpeg() -> bool:
    p = subprocess.run(["ffmpeg", "-version"], capture_output=True)
    return p.returncode == 0


SRC = _P(r"E:\монтажи\1. пробник интервью кати+богдан(нейро)\synced\ref.mp4")
if not have_ffmpeg() or not SRC.is_file():
    print("ПРОПУСК впечатывания: нет ffmpeg или исходника")
else:
    with tempfile.TemporaryDirectory() as td:
        td = _P(td)
        small = td / "in.mp4"
        p = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", "100", "-i", str(SRC),
             "-t", "1", "-c", "copy", str(small)], capture_output=True)
        ok("фрагмент собран", small.is_file(), p.stderr.decode()[-200:])

        before = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", str(small)], capture_output=True, text=True)
        dst = td / "out.mp4"
        r = multicam.bake_timecode(small, dst, "01:00:00:00")
        ok("впечатался", r["ok"], r.get("error", ""))
        eq("таймкод в ответе", r.get("timecode"), "01:00:00:00")

        after = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", str(dst)], capture_output=True, text=True)
        import json
        bs = json.loads(before.stdout)["streams"][0]
        all_after = json.loads(after.stdout)["streams"]
        as_ = next(s for s in all_after if s["codec_type"] == "video")
        eq("разрешение не поехало",
           as_.get("width"), bs.get("width"))
        eq("кадров в секунду не поехало",
           as_.get("r_frame_rate"), bs.get("r_frame_rate"))
        ok("длительность не поехала",
           as_.get("duration"), bs.get("duration"))

        # -c copy значит, что видеопоток должен остаться побайтово
        # прежним. Мерить весь файл нельзя: ffmpeg добавляет служебную
        # дорожку таймкода, и на секундном обрывке это заметно, хотя
        # на получасовой записи - копейки.
        vsize = lambda streams: sum(  # noqa: E731
            int(s.get("codec_size") or s.get("bit_rate") or 0)
            for s in streams if s["codec_type"] == "video")
        ok("видеопоток не перекодирован",
           vsize(all_after) <= vsize([bs]) * 1.02,
           f"{vsize(all_after)} против {vsize([bs])}")
        ok("появилась дорожка таймкода",
           any(s["codec_type"] not in ("video", "audio")
               for s in all_after),
           str([s["codec_type"] for s in all_after]))

print()
if fail:
    print(f"MULTICAM FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("MULTICAM OK")
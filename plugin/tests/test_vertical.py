# -*- coding: utf-8 -*-
"""Вертикаль 9:16: кадр, скорость и что уходит в Premiere.

Проверяется без ffmpeg - чистая математика фильтра и проброс
настроек. Сам разрез проверяется сквозным прогоном вручную: он
перекодирует видео и на тестах занимает минуты.
"""
import shutil as _shutil
import subprocess as _subprocess
import sys
import tempfile as _tempfile
from pathlib import Path
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src" / "ui"))
import bootstrap  # noqa: F401,F403

import vertical

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


# --- фильтр кадра ---
f = vertical.crop_filter(1080, 1920)
ok("режем по центру под 9:16", "crop=trunc(ih*9/16/2)*2:ih" in f, f)
ok("и растягиваем в 1080x1920", "scale=1080:1920" in f, f)
ok("ширина реза чётная", "trunc(ih*9/16/2)*2" in f, f)
ok("без скорости setpts нет", "setpts" not in f, f)

fast = vertical.crop_filter(1080, 1920, 1.5)
ok("ускорение меняет длительность в setpts", "setpts=0.6666" in fast, fast)
ok("ускорение 1.0 ничего не добавляет",
   "setpts" not in vertical.crop_filter(1080, 1920, 1.0), "")
ok("даже мелкое отклонение уважается буквально",
   "setpts" in vertical.crop_filter(1080, 1920, 1.01), "")
ok("бессмысленная скорость не ломает фильтр",
   vertical.crop_filter(1080, 1920, 0) == f
   and vertical.crop_filter(1080, 1920, -1) == f,
   vertical.crop_filter(1080, 1920, 0))

# --- длительность куска берётся из in/out ---
ok("длительность из in/out",
   vertical.clip_duration({"in": "10", "out": "25", "duration": "99"}) == 15.0,
   str(vertical.clip_duration({"in": "10", "out": "25", "duration": "99"})))
ok("без in/out берётся колонка duration",
   vertical.clip_duration({"duration": "7.5"}) == 7.5,
   str(vertical.clip_duration({"duration": "7.5"})))
ok("мусор в in/out не роняет",
   vertical.clip_duration({"in": "", "out": "", "duration": "3"}) == 3.0,
   str(vertical.clip_duration({"in": "", "out": "", "duration": "3"})))
ok("пустая строка даёт ноль",
   vertical.clip_duration({}) == 0.0, str(vertical.clip_duration({})))

# --- проброс настроек в шаги ---
import steps  # noqa: E402

va = steps.vertical_args("rz/selects.csv", "proj", 1080, 1920, 1.5)
ok("шаг вертикали указывает на selects", "--selects" in va, str(va))
ok("шаг вертикали указывает на выходную папку",
   "--outdir" in va, str(va))
ok("скорость уходит в аргументы",
   "--speed" in va and "1.5" in va, str(va))
ok("размер кадра уходит в аргументы",
   "1080" in va and "1920" in va, str(va))

# --- вертикаль обязана объявить секвенцию вертикальной ---
pa = steps.premiere_args("vert/selects.csv", "proj", width=1080, height=1920)
ok("премьера получает вертикальный кадр",
   "--width" in pa and "1080" in pa and "--height" in pa and "1920" in pa,
   str(pa))
plain = steps.premiere_args("rz/selects.csv", "proj")
ok("без вертикали размер не задаётся", "--width" not in plain, str(plain))

# --- регрессия: кадр приходит из пайпа, а не из исходника ---
# Один раз тут был замаскирован: -map 0:v тянул картинку из исходного
# файла, а не из пайпа, и на выходе получался полный горизонтальный кадр,
# растянутый в вертикаль. Структурно такой XML выглядит правильно -
# размеры верные, <file> на месте. Видно только глазами, поэтому
# проверка делается по цвету: исходник из трёх полос, режем одну.
def _bands(path, n):
    """Доля красного, зелёного и синего в кадре n."""
    import subprocess
    import numpy as np
    import cv2
    png = _P(path).with_suffix(".probe.png")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path),
                    "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", str(png)],
                   capture_output=True)
    img = cv2.imdecode(np.frombuffer(png.read_bytes(), np.uint8),
                       cv2.IMREAD_COLOR)
    b, g, r = img[:, :, 0].astype(int), img[:, :, 1].astype(int), \
        img[:, :, 2].astype(int)
    png.unlink(missing_ok=True)
    return {"r": (r > 120).mean(), "g": (g > 120).mean(), "b": (b > 120).mean()}


if _shutil.which("ffmpeg"):
    _tmp = Path(_tempfile.mkdtemp(prefix="autocut_vert_"))
    _src = _tmp / "src.mp4"
    _out = _tmp / "out.mp4"
    _subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=red:s=640x360:r=25:d=1",
         "-f", "lavfi", "-i", "color=c=green:s=640x360:r=25:d=1",
         "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=25:d=1",
         "-filter_complex", "[0:v][1:v][2:v]hstack=inputs=3[v]",
         "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(_src)],
        capture_output=True)
    _row = {"in": "0", "out": "1", "duration": "1", "audio": ""}
    # окно заведомо на красной полосе (0..640)
    _wins = [(0, 0, 360, 640)] * 25
    _err = vertical.render_tracked(_src, _out, _row, 360, 640, 1.0,
                                    _wins, 1920, 360, 25.0, "")
    ok("кусок с ведением камеры собрался", not _err, _err)
    if not _err:
        _mix = _bands(_out, 5)
        ok("в кадре та полоса, которую вырезали",
           _mix["r"] > 0.9 and _mix["g"] < 0.05, str(_mix))
        ok("соседние полосы в кадр не попали",
           _mix["g"] < 0.05 and _mix["b"] < 0.05, str(_mix))
    _shutil.rmtree(_tmp, ignore_errors=True)
else:
    print("пропуск сквозной проверки кадра: нет ffmpeg в PATH")

print()
if fail:
    print(f"VERTICAL FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("VERTICAL OK")
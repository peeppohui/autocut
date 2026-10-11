# -*- coding: utf-8 -*-
"""Тест предпросмотра: превью-кадры и сборка рыбы в mp4.

ffmpeg и видео тут не трогаем - проверяем то, что ломается молча:
списки для конкатенатора и разбор selects.csv. Оба бага этой версии
выглядели как «пустой результат» без единой ошибки.
"""
import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
import bootstrap  # noqa: F401,F403

import preview

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


CSV = ("file,plan,hero,duration,in,out,desc,path,audio\n"
       "cam.mp4,средний1,гость,2.0,10.0,12.0,\"ГОСТЬ: раз\","
       "E:/proj/cam.mp4,E:/proj/_guest.wav\n"
       "ref.mp4,общий,ведущий,3.0,20.0,23.0,\"ВЕДУЩИЙ: два\","
       "E:/proj/ref.mp4,E:/proj/_host.wav\n"
       "broll.mp4,перебивка,-,1.6,,\n"          # без таймкода - пропускаем
       "cam.mp4,средний1,гость,0,5.0,5.0,\"нулевой\","
       "E:/proj/cam.mp4,E:/proj/_guest.wav\n")  # нулевой - тоже пропускаем

tmp = _P(__file__).resolve().parent / "_t_sel.csv"
tmp.write_text(CSV, encoding="utf-8-sig")
rows = preview.load_selects(tmp)

ok("без таймкода пропущен", len(rows) == 2, str(len(rows)))
ok("in/out стали числами", isinstance(rows[0]["in"], float))

# --- списки для ffmpeg: главное, что они РАЗНЫЕ ---
vlist = preview._ffconcat(rows, "path", tmp.parent / "_v.ffconcat")
alist = preview._ffconcat(rows, "audio", tmp.parent / "_a.ffconcat")

ok("списки лежат по разным именам", vlist != alist, f"{vlist} / {alist}")
v_txt = vlist.read_text(encoding="utf-8")
a_txt = alist.read_text(encoding="utf-8")
ok("видеосписок ссылается на камеры", "cam.mp4" in v_txt and "ref.mp4" in v_txt)
ok("звуковой список ссылается на микрофоны",
   "_guest.wav" in a_txt and "_host.wav" in a_txt)
ok("в списке есть inpoint/outpoint",
   "inpoint 10.000" in v_txt and "outpoint 12.000" in v_txt, v_txt[:200])
ok("в списке есть длительность", "duration 2.000" in v_txt)
ok("первая строка - заголовок ffconcat",
   v_txt.startswith("ffconcat version 1.0"))

# вторая запись тоже присутствует: раньше список перезаписывался
ok("обе записи в списке", v_txt.count("file '") == 2, str(v_txt.count("file '")))

# --- имя файла превью стабильно и зависит от точек ---
n1 = preview.thumb_name(rows[0])
same = dict(rows[0])
same["in"], same["out"] = rows[0]["in"], rows[0]["out"]
ok("имя превью устойчиво", n1 == preview.thumb_name(same))
moved = dict(rows[0])
moved["in"] = rows[0]["in"] + 5
ok("сдвинули точку - имя другое", n1 != preview.thumb_name(moved))
ok("имя заканчивается .png", n1.endswith(".png"), n1)

# --- сборка превью на пустом входе не должна падать ---
bad = tmp.parent / "_t_empty.csv"
bad.write_text("file,path\nx.mp4,\n", encoding="utf-8-sig")
res = preview.make_preview(bad, tmp.parent / "_x.mp4")
ok("пустой selects даёт понятную ошибку", not res["ok"] and res["error"],
   str(res))

for f in (vlist, alist, tmp, bad):
    try:
        f.unlink()
    except OSError:
        pass

print()
if fail:
    print(f"PREVIEW FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("PREVIEW OK")
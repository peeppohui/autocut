# -*- coding: utf-8 -*-
"""Тест мультикама в FCP7 XML.

Главное, что тут проверяется: оба угла получают ОДИН И ТОТ ЖЕ in/out.
Это работает только потому, что после sync.py камеры лежат на общей
шкале времени. Если разъедутся - Premiere покажет второй ракурс
с другого момента, и склейка поедет.
"""
import copy
import csv
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
import bootstrap  # noqa: F401,F403

import premiere

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


with tempfile.TemporaryDirectory() as td:
    td = _P(td)
    # два настоящих файла нужны: экспортёр проверяет существование второго ракурса
    a = td / "cam.mp4"
    b = td / "ref.mp4"
    for p in (a, b):
        p.write_bytes(b"\x00" * 16)

    mic = td / "_guest.wav"
    mic.write_bytes(b"\x00" * 16)
    host = td / "_host.wav"
    host.write_bytes(b"\x00" * 16)

    csv_path = td / "selects.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "plan", "hero", "duration", "in", "out",
                    "desc", "path", "audio", "alt", "alt_path"])
        w.writerow(["cam.mp4", "средний1", "гость", 4.0, 10, 110,
                    "ГОСТЬ: речь", str(a), str(mic), "ref.mp4", str(b)])
        w.writerow(["ref.mp4", "общий", "ведущий", 3.0, 200, 290,
                    "ВЕДУЩИЙ: ответ", str(b), str(host), "cam.mp4", str(a)])

    clips = premiere.resolve_media(
        premiere.load_timeline(csv_path), td)

    # --- без флага: обычная секвенция, как раньше ---
    plain = premiere.build_fcp7(list(clips), fps=25)
    ok("без флага мультикамов нет", len(plain.findall(".//multiclip")) == 0,
       str(len(plain.findall(".//multiclip"))))
    ok("без флага файлы у клипов на месте",
       len(plain.findall(".//video/track/clipitem/file")) == 2)

    # --- с флагом: мультикам ---
    mc = premiere.build_fcp7(list(clips), fps=25, multicam=True)
    mcs = mc.findall(".//multiclip")
    ok("мультикамов столько же, сколько видеоклипов", len(mcs) == 2, str(len(mcs)))

    for i, m in enumerate(mcs, 1):
        angles = m.findall("angle")
        if len(angles) != 2:
            ok(f"мультикам {i}: два угла", False, str(len(angles)))
            continue
        ok(f"мультикам {i}: два угла", True)

        ins = [a.find("clip/in").text for a in angles]
        outs = [a.find("clip/out").text for a in angles]
        ok(f"мультикам {i}: у углов одинаковый in", len(set(ins)) == 1, str(ins))
        ok(f"мультикам {i}: у углов одинаковый out", len(set(outs)) == 1, str(outs))

        active = [a for a in angles
                  if a.find("activevideoangle") is not None]
        ok(f"мультикам {i}: ровно один активный угол", len(active) == 1,
           str(len(active)))

        files = [a.find("clip/file/pathurl").text for a in angles]
        ok(f"мультикам {i}: углы ссылаются на разные файлы",
           len(set(files)) == 2, str(files))

        # у активного угла должен быть именно тот файл, что выбрал авто-монтаж
        want = "cam.mp4" if i == 1 else "ref.mp4"
        got = active[0].find("clip/name").text if active else ""
        ok(f"мультикам {i}: активен угол говорящего", got == want,
           f"активен {got}, ждали {want}")

    ok("synctype = 1 (по In)", all(
        m.find("synctype").text == "1" for m in mcs))
    ok("mediadelay проставлен", all(
        c.find("mediadelay") is not None
        for c in mc.findall(".//video/track/clipitem")))

    # --- целостность XML ---
    ids = [e.get("id") for e in mc.iter() if e.get("id")]
    ok("нет дублей id", len(ids) == len(set(ids)),
       f"{len(ids)} против {len(set(ids))}")
    refs = {l.text for l in mc.iter("linkclipref")}
    ok("нет висячих ссылок", not (refs - set(ids)), str(sorted(refs - set(ids))))
    ok("check_xml доволен", not premiere.check_xml(mc),
       str(premiere.check_xml(mc)))

    # --- если второго ракурса нет, мультикам не строится ---
    # важно: глубокая копия. list() копирует только список, а словари
    # остаются те же - и обнуление alt уезжало в исходные данные
    clips2 = copy.deepcopy(clips)
    clips2[0]["alt"] = ""
    clips2[0]["alt_path"] = ""
    mc2 = premiere.build_fcp7(clips2, fps=25, multicam=True)
    ok("без второго ракурса - обычный клип",
       len(mc2.findall(".//multiclip")) == 1,
       str(len(mc2.findall(".//multiclip"))))

    # =========================================================
    # --stacked: две дорожки вместо multiclip
    # Premiere 26 не превращает multiclip в мультикамерную секвенцию,
    # поэтому ракурсы кладутся на две дорожки, и переключение идёт
    # через Toggle Switches.
    # =========================================================
    st = premiere.build_fcp7(list(clips), fps=25, stacked=True)
    tracks = st.findall(".//video/track")
    ok("две видеодорожки", len(tracks) == 2, str(len(tracks)))
    ok("мультикамов при этом нет", len(st.findall(".//multiclip")) == 0)

    t1 = tracks[0].findall("clipitem")
    t2 = tracks[1].findall("clipitem")
    ok("на дорожках поровну клипов", len(t1) == len(t2),
       f"{len(t1)} против {len(t2)}")

    bad = []
    for a, b in zip(t1, t2):
        for f in ("start", "end", "in", "out"):
            if a.find(f).text != b.find(f).text:
                bad.append(f"{a.find('name').text}:{f}")
    ok("у ракурсов совпадает тайминг", not bad, str(bad[:4]))
    ok("ракурсы разные",
       all(a.find("name").text != b.find("name").text
           for a, b in zip(t1, t2)))
    ok("на втором ракурсе метка",
       t2[0].find("labels/label2") is not None)
    ok("на втором ракурсе пояснение",
       t2[0].find("logginginfo/lognote") is not None)

    # звук у второго ракурса свой быть не должен - он с микрофонов
    audio_names = {c.find("name").text for c in st.findall(".//audio/track/clipitem")}
    ok("на аудиодорожке только микрофоны",
       all(n.endswith(".wav") for n in audio_names), str(audio_names))

print()
if fail:
    print(f"MULTICAM FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("MULTICAM OK")
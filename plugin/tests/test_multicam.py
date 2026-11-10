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

    # --- обычная секвенция: у клипов на месте файлы ---
    plain = premiere.build_fcp7(list(clips), fps=25)
    ok("у клипов на месте файлы",
       len(plain.findall(".//video/track/clipitem/file")) == 2)

    # --- целостность XML ---
    ids = [e.get("id") for e in plain.iter() if e.get("id")]
    ok("нет дублей id", len(ids) == len(set(ids)),
       f"{len(ids)} против {len(set(ids))}")
    refs = {l.text for l in plain.iter("linkclipref")}
    ok("нет висячих ссылок", not (refs - set(ids)), str(sorted(refs - set(ids))))
    ok("check_xml доволен", not premiere.check_xml(plain),
       str(premiere.check_xml(plain)))

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

    # У клипа ОБЯЗАН быть <file>, иначе Premiere не импортирует файл вовсе.
    # Здесь это ломалось: ветка stacked создавала второй ракурс и забывала
    # прописать файл основному клипу - V1 шла вообще без ссылки на медиа.
    nofile = [c.find("name").text for t in tracks
              for c in t.findall("clipitem") if c.find("file") is None]
    ok("у каждого клипа есть <file>", not nofile, str(nofile[:4]))
    ok("второй ракурс тоже ссылается на файл",
       all(c.find("file") is not None for c in t2))

    # =========================================================
    # Маркеры лежат на уровне секвенции. Внутри <clipitem> DTD их
    # разрешает, но Premiere оттуда не показывает - панель Markers
    # оставалась пустой, а с маркерами видно, почему склеено.
    # =========================================================
    mks = st.findall("sequence/marker")
    ok("маркеры на уровне секвенции", len(mks) == len(clips), str(len(mks)))
    ok("внутри клипов маркеров не осталось",
       not st.findall(".//clipitem/marker"))
    ok("у маркера есть имя и текст",
       bool(mks[0].findtext("name")) and bool(mks[0].findtext("comment")),
       f"{mks[0].findtext('name')} / {mks[0].findtext('comment')}")
    ok("у маркера есть таймкод", bool(mks[0].findtext("in")), "")
    ok("маркеров нет без --no-markers",
       not premiere.build_fcp7(list(clips), fps=25, stacked=True,
                               markers=False).findall("sequence/marker"))

    # =========================================================
    # Раздельные микрофоны: каждому источнику звука своя дорожка.
    # Раньше все клипы делили одну A1, и ведущий с гостем в постпродакшне
    # были неразделимы.
    # =========================================================
    at = st.findall(".//audio/track")
    ok("микрофоны на разных дорожках", len(at) == 2, str(len(at)))
    got = [sorted({c.findtext("name") for c in tr.findall("clipitem")})
           for tr in at]
    ok("на каждой дорожке свой микрофон",
       got == [["_guest.wav"], ["_host.wav"]], str(got))
    # индекс дорожки в <link> обязан совпадать с дорожкой самого клипа
    wrong = []
    for k, tr in enumerate(at, 1):
        for c in tr.findall("clipitem"):
            for lk in c.findall("link"):
                if lk.findtext("mediatype") == "audio" \
                        and int(lk.findtext("trackindex")) != k:
                    wrong.append((c.get("id"), k))
    ok("trackindex в ссылке совпадает с дорожкой", not wrong, str(wrong[:4]))
    ok("на дорожках микрофонов нет totalExplodedTrackCount",
       all("totalExplodedTrackCount" not in tr.attrib for tr in at),
       str([tr.attrib for tr in at]))

    # =========================================================
    # Перебивка без микрофона: звук нужен, поэтому она линкуется сама
    # на свой файл и встаёт на отдельную дорожку. Это осознанное
    # решение (см. AGENTS.md), а не случайность: нельзя «починить»
    # клиент без звука перебивки, потому что в новостном сюжете он
    # нужен редактору.
    # =========================================================
    broll = td / "broll.mp4"
    broll.write_bytes(b"\x00" * 16)
    vo = td / "vo.wav"
    vo.write_bytes(b"\x00" * 16)

    story = td / "story.csv"
    with open(story, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "plan", "hero", "duration", "in", "out",
                    "desc", "path", "audio", "track"])
        # синхрон: микрофон прописан явно
        w.writerow(["cam.mp4", "средний2", "гость", 4.0, 10, 110,
                    "ГОСТЬ: речь", str(a), str(mic), "V1"])
        # перебивка: колонка audio пуста, но звук у неё нужен
        w.writerow(["broll.mp4", "", "", 3.0, 0, 30,
                    "перебивка", str(broll), "", "V1"])
        # закадр: самостоятельная звуковая дорожка
        w.writerow(["vo.wav", "", "закадр", 5.0, 0, 125,
                    "закадр", str(vo), "", "A2"])

    story_clips = premiere.resolve_media(
        premiere.load_timeline(story), td)
    story_xml = premiere.build_fcp7(list(story_clips), fps=25)
    st_at = story_xml.findall(".//audio/track")
    st_names = [sorted({c.findtext("name") for c in tr.findall("clipitem")})
                for tr in st_at]

    ok("перебивка получила свою звуковую дорожку",
       ["broll.mp4"] in st_names, str(st_names))
    ok("микрофон синхрона остался на своей дорожке",
       ["_guest.wav"] in st_names, str(st_names))
    ok("закадр не потерялся", ["vo.wav"] in st_names, str(st_names))
    ok("три звуковые дорожки: синхрон, перебивка, закадр",
       len(st_at) == 3, str(len(st_at)))
    # самостоятельные дорожки объявляются после связанных, поэтому
    # закадр с колонкой A2 физически встаёт на A3. Номера из settings
    # описывают пожелание, а не адрес в готовой секвенции.
    ok("закадр объявлен после связанного звука",
       st_names.index(["vo.wav"]) == 2, str(st_names))
    ok("у сюжета не осталось totalExplodedTrackCount",
       all("totalExplodedTrackCount" not in tr.attrib for tr in st_at),
       str([tr.attrib for tr in st_at]))

    # =========================================================
    # Звук рекордера: в плане исходный REC и его собственные in/out,
    # а не нарезанный WAV. Все куски одного рекордера должны лежать
    # на одной A2, иначе каждый вырезанный файл создаёт свою дорожку.
    # =========================================================
    rec1 = td / "REC00016.WAV"
    rec2 = td / "REC00006.WAV"
    for p in (rec1, rec2):
        p.write_bytes(b"\x00" * 16)

    rec_csv = td / "rec.csv"
    with open(rec_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "plan", "hero", "duration", "in", "out",
                    "desc", "path", "audio", "track", "audio_in",
                    "audio_out", "audio_duration", "audio_track"])
        w.writerow(["cam.mp4", "средний2", "гость", 4.0, 1, 5,
                    "ГОСТЬ: речь", str(a), str(rec1), "V1", 6.2, 10.2,
                    60.0, "A2"])
        w.writerow(["cam.mp4", "средний2", "гость", 4.0, 5, 9,
                    "ГОСТЬ: речь", str(a), str(rec1), "V1", 20.2, 24.2,
                    60.0, "A2"])
        w.writerow(["cam.mp4", "средний2", "гость", 4.0, 9, 13,
                    "ГОСТЬ: речь", str(a), str(rec2), "V1", 451.76, 455.76,
                    600.0, "A2"])

    rec_clips = premiere.resolve_media(
        premiere.load_timeline(rec_csv), td)
    rec_xml = premiere.build_fcp7(list(rec_clips), fps=25)
    rec_at = rec_xml.findall(".//audio/track")
    ok("рекордер занимает одну дорожку A2", len(rec_at) == 1,
       str(len(rec_at)))
    rec_items = rec_at[0].findall("clipitem") if rec_at else []
    ok("все куски рекордера на одной дорожке", len(rec_items) == 3,
       str(len(rec_items)))
    pairs = [(c.findtext("in"), c.findtext("out")) for c in rec_items]
    ok("in/out звука взяты из рекордера, а не из камеры",
       pairs == [("155", "255"), ("505", "605"), ("11294", "11394")],
       str(pairs))
    fdurs = [c.findtext("file/duration") for c in rec_items]
    ok("длительность файла — исходника, а не камерного куска",
       fdurs == ["1500", "1500", "15000"], str(fdurs))
    ok("XML с рекордером проходит check_xml",
       not premiere.check_xml(rec_xml), str(premiere.check_xml(rec_xml)))

    # =========================================================
    # Раскладку правят руками, часто в Excel. Лишняя запятая в строке
    # даёт колонок больше, чем в заголовке, и DictReader складывает их
    # в список. Раньше экспорт на этом падал с AttributeError - то
    # есть на самом последнем шаге, после всей работы.
    # =========================================================
    odd = td / "odd.csv"
    with open(odd, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "duration", "in", "out", "path"])
        w.writerow(["cam.mp4", 4.0, 10, 110, str(a), "лишний", "ещё"])
    odd_clips = premiere.resolve_media(premiere.load_timeline(odd), td)
    ok("лишние колонки не роняют экспорт", len(odd_clips) == 1,
       str(len(odd_clips)))
    ok("и кусок остался целым", odd_clips[0]["duration"] == 4.0,
       str(odd_clips[0]["duration"]))
    odd_xml = premiere.build_fcp7(list(odd_clips), fps=25)
    ok("XML с такой раскладкой собирается",
       not premiere.check_xml(odd_xml), str(premiere.check_xml(odd_xml)))

    # =========================================================
    # Колонка pos: соседние куски обязаны сцепляться по кадрам.
    # Раньше начало считали round(pos*fps), а конец независимо из
    # in/out — соседи расходились на ±1 кадр: перекрытия и дырки.
    # Теперь конец каждого вычисляется из pos + длительность, а окно
    # источника дожимается до окна таймлайна. Заодно: in=0 у
    # источника не должен превращаться в 1.
    # =========================================================
    pos_csv = td / "pos.csv"
    with open(pos_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "plan", "hero", "duration", "in", "out",
                    "desc", "path", "track", "pos"])
        w.writerow(["cam.mp4", "нарезка", "гость", 1.234, 10, 41,
                    "вырезка", str(a), "V1", 0.0])
        w.writerow(["ref.mp4", "нарезка", "ведущий", 2.0, 0, 50,
                    "вырезка", str(b), "V1", 1.234])
        w.writerow(["cam.mp4", "нарезка", "гость", 2.0, 5, 55,
                    "вырезка", str(a), "V1", 3.234])
    pos_clips = premiere.resolve_media(
        premiere.load_timeline(pos_csv), td)
    pos_xml = premiere.build_fcp7(list(pos_clips), fps=25)
    items = pos_xml.findall(".//video/track/clipitem")
    ok("все три куска с pos на одной дорожке", len(items) == 3,
       str(len(items)))
    ends = [(int(x.find("start").text), int(x.find("end").text))
            for x in items]
    ok("соседи сцеплены по кадрам",
       all(ends[i][1] == ends[i + 1][0] for i in range(len(ends) - 1)),
       str(ends))
    wins = [int(x.find("out").text) - int(x.find("in").text) for x in items]
    slots = [e - s for s, e in ends]
    ok("окно источника равно окну таймлайна", wins == slots,
       "{} против {}".format(wins, slots))
    ok("in=0 не превращается в 1", items[1].find("in").text == "0",
       items[1].find("in").text)
    ok("XML с pos проходит check_xml",
       not premiere.check_xml(pos_xml), str(premiere.check_xml(pos_xml)))

print()
if fail:
    print(f"MULTICAM FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("MULTICAM OK")
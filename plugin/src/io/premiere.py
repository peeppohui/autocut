#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoCut Premiere — выгрузка готового таймлайна в Adobe Premiere Pro.
Читает CSV таймлайна (после autocut --auto) и строит:
  1) FCP7 XML (xmeml v5) — File > Import в Premiere, звук сохраняется;
  2) EDL CMX3600 — запасной вариант (File > Import).

Использование:
  python premiere.py --timeline timeline.csv --media-dir E:\\Съёмка\\день1 --fps 25
  # получится premiere.xml + premiere.edl рядом
  # В Premiere: File > Import > premiere.xml > откроется бин с секвенцией AutoCut

Ожидаемый CSV: n,file,plan,hero,duration,desc[,path]
Если есть колонка path — берём путь оттуда, иначе склеиваем media-dir + file.
"""

from __future__ import annotations
import argparse
import csv
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import quote as _quote
from xml.dom import minidom

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bootstrap  # noqa: F401  — регистрирует src/modes в sys.path (нужен autocut)

media_dir_global = Path(".")
try:
    from autocut import REC_DURATION
except ImportError:
    REC_DURATION = {}


def load_timeline(path: Path) -> List[Dict]:
    rows: List[Dict] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            # нормализуем ключи к нижнему регистру
            row = { (k or "").strip().lower(): (v or "").strip() for k, v in row.items() }
            if not row.get("file"):
                continue
            try:
                dur = float((row.get("duration") or "3").replace(",", "."))
            except ValueError:
                dur = 3.0
            def _num(key):
                v = (row.get(key) or "").strip()
                if not v:
                    return None
                try:
                    return float(v.replace(",", "."))
                except ValueError:
                    return None
            rows.append({
                "file": row["file"],
                "plan": row.get("plan", ""),
                "hero": row.get("hero", ""),
                "duration": max(0.5, dur),
                "desc": row.get("desc", ""),
                "path": row.get("path", ""),
                "src_in": _num("in"),    # точный вход (сек), из interview.py
                "src_out": _num("out"),  # точный выход (сек)
                # звук для A1 отдельно (микрофон говорящего из interview.py);
                # пусто = звук из видеофайла
                "audio": (row.get("audio") or "").strip(),
            })
    return rows


def resolve_media(clips: List[Dict], media_dir: Path) -> List[Dict]:
    global media_dir_global
    media_dir_global = Path(media_dir)
    out = []
    for c in clips:
        if c["path"]:
            p = Path(c["path"])
            if not p.is_absolute():
                p = media_dir / p  # относительный path — от media-dir, не от CWD
        else:
            p = media_dir / c["file"]
        # не требуем существования — Premiere всё равно попросит relink,
        # но предупреждаем
        c = dict(c)
        c["abspath"] = str(p.absolute())
        c["exists"] = p.exists()
        out.append(c)
    return out


def path_to_fileurl(abspath: str) -> str:
    # Формат, который понимает Premiere в FCP7 XML:
    #   file://localhost/E:/Папка/файл.mp4
    # Двоеточие после диска НЕ кодируем, пробелы/кириллицу — да.
    # (urllib pathname2url на Windows даёт лишний префикс /// и ломает линковку.)
    p = abspath.replace("\\", "/")
    return "file://localhost/" + _quote(p, safe="/:")


def frames(sec: float, fps: int) -> int:
    return max(1, int(round(sec * fps)))


def tc(fr: int, fps: int) -> str:
    s = fr // fps
    f = fr % fps
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def take_window(c: Dict, fps: int, trim_rec: bool):
    """Сколько берём из исходника: (orig, src_in, src_out, used) в кадрах.

    Без trim_rec — весь файл целиком. С trim_rec — не длиннее верхней границы
    рекомендованной длительности плана (REC_DURATION), вырезка из середины:
    края исходников чаще всего дёрганые (старт/стоп записи).
    """
    orig = frames(c["duration"], fps)
    # Точные куски из interview.py (in/out заданы): берём как есть, trim_rec их не касается.
    # duration там = длина куска, а файл заведомо длиннее (in/out взяты из него).
    if c.get("src_in") is not None and c.get("src_out") is not None \
            and c["src_out"] > c["src_in"]:
        src_in = frames(c["src_in"], fps)
        src_out = frames(c["src_out"], fps)
        return max(src_out, orig), src_in, src_out, src_out - src_in
    if trim_rec and c.get("plan") in REC_DURATION:
        cap_f = frames(REC_DURATION[c["plan"]][1], fps)
        if orig > cap_f:
            src_in = (orig - cap_f) // 2
            return orig, src_in, src_in + cap_f, cap_f
    return orig, 0, orig, orig


def _video_file_el(fid: str, c: Dict, orig: int, fps: int, width: int, height: int) -> ET.Element:
    """Полное описание видеофайла как в родном экспорте FCP7."""
    f = ET.Element("file", id=fid)
    ET.SubElement(f, "name").text = c["file"]
    ET.SubElement(f, "pathurl").text = path_to_fileurl(c["abspath"])
    f.append(_rate_el(fps))
    ET.SubElement(f, "duration").text = str(orig)
    ET.SubElement(f, "timecode").append(_rate_el(fps))
    fmc = f.find("timecode")
    ET.SubElement(fmc, "string").text = "00:00:00:00"
    ET.SubElement(fmc, "frame").text = "0"
    ET.SubElement(fmc, "displayformat").text = "NDF"
    fmedia = ET.SubElement(f, "media")
    fv = ET.SubElement(fmedia, "video")
    fsc = ET.SubElement(fv, "samplecharacteristics")
    fsc.append(_rate_el(fps))
    ET.SubElement(fsc, "width").text = str(width)
    ET.SubElement(fsc, "height").text = str(height)
    ET.SubElement(fsc, "anamorphic").text = "FALSE"
    ET.SubElement(fsc, "pixelaspectratio").text = "square"
    ET.SubElement(fsc, "fielddominance").text = "none"
    ET.SubElement(fsc, "colordepth").text = "24"
    fa = ET.SubElement(fmedia, "audio")
    fasc = ET.SubElement(fa, "samplecharacteristics")
    ET.SubElement(fasc, "depth").text = "16"
    ET.SubElement(fasc, "samplerate").text = "48000"
    ET.SubElement(fa, "channelcount").text = "2"
    return f


def _audio_file_el(fid: str, c: Dict, orig: int, fps: int) -> ET.Element:
    """Полное описание аудиофайла. ВАЖНО: id уникальный (не совпадает
    с id видео-описания того же файла) и блок полный — дублирующиеся
    и урезанные <file> молча роняют импорт в Premiere."""
    f = ET.Element("file", id=fid)
    ET.SubElement(f, "name").text = c["file"]
    ET.SubElement(f, "pathurl").text = path_to_fileurl(c["abspath"])
    f.append(_rate_el(fps))
    ET.SubElement(f, "duration").text = str(orig)
    ET.SubElement(f, "timecode").append(_rate_el(fps))
    fmc = f.find("timecode")
    ET.SubElement(fmc, "string").text = "00:00:00:00"
    ET.SubElement(fmc, "frame").text = "0"
    ET.SubElement(fmc, "displayformat").text = "NDF"
    fmedia = ET.SubElement(f, "media")
    fa = ET.SubElement(fmedia, "audio")
    fasc = ET.SubElement(fa, "samplecharacteristics")
    ET.SubElement(fasc, "depth").text = "16"
    ET.SubElement(fasc, "samplerate").text = "48000"
    ET.SubElement(fa, "channelcount").text = "2"
    return f


def build_fcp7(clips: List[Dict], fps: int = 25, width: int = 1920, height: int = 1080,
               seq_name: str = "AutoCut", trim_rec: bool = False,
               audios: Optional[List[str]] = None) -> ET.Element:
    xmeml = ET.Element("xmeml", version="5")
    seq = ET.SubElement(xmeml, "sequence", id="sequence-1", name=seq_name)
    ET.SubElement(seq, "name").text = seq_name

    for c in clips:
        c["_orig"], c["_in"], c["_out"], c["_used"] = take_window(c, fps, trim_rec)
    total = sum(c["_used"] for c in clips)
    ET.SubElement(seq, "duration").text = str(total)
    rate = ET.SubElement(seq, "rate")
    ET.SubElement(rate, "timebase").text = str(fps)
    ET.SubElement(rate, "ntsc").text = "FALSE"
    ET.SubElement(seq, "timecode").append(_rate_el(fps))
    tc_el = seq.find("timecode")
    ET.SubElement(tc_el, "string").text = "00:00:00:00"
    ET.SubElement(tc_el, "frame").text = "0"
    ET.SubElement(tc_el, "displayformat").text = "NDF"

    media = ET.SubElement(seq, "media")

    # ---- video ----
    video = ET.SubElement(media, "video")
    fmt = ET.SubElement(video, "format")
    sc = ET.SubElement(fmt, "samplecharacteristics")
    sc.append(_rate_el(fps))
    ET.SubElement(sc, "width").text = str(width)
    ET.SubElement(sc, "height").text = str(height)
    ET.SubElement(sc, "anamorphic").text = "FALSE"
    ET.SubElement(sc, "pixelaspectratio").text = "square"
    ET.SubElement(sc, "fielddominance").text = "none"
    ET.SubElement(sc, "colordepth").text = "24"

    vtrack = ET.SubElement(video, "track")
    ET.SubElement(vtrack, "enabled").text = "TRUE"
    ET.SubElement(vtrack, "locked").text = "FALSE"

    # ---- audio (стерео, 48кГц) ----
    audio = ET.SubElement(media, "audio")
    afmt = ET.SubElement(audio, "format")
    asc = ET.SubElement(afmt, "samplecharacteristics")
    ET.SubElement(asc, "depth").text = "16"
    ET.SubElement(asc, "samplerate").text = "48000"
    atrack = ET.SubElement(audio, "track", currentExplodedTrackIndex="0", totalExplodedTrackCount="1")
    ET.SubElement(atrack, "enabled").text = "TRUE"
    ET.SubElement(atrack, "locked").text = "FALSE"
    btrack = None
    audios = [a for a in (audios or []) if a]
    extra_tracks = []
    for _ in audios:
        # Целые дорожки: микрофоны, подложка (уровень/дакинг уже запечены
        # в файлах, в XML идут как есть, на всю длину). A2, A3, ...
        tr = ET.SubElement(audio, "track")
        ET.SubElement(tr, "enabled").text = "TRUE"
        ET.SubElement(tr, "locked").text = "FALSE"
        extra_tracks.append(tr)

    t = 0
    for i, c in enumerate(clips, 1):
        orig, src_in, src_out, d = c["_orig"], c["_in"], c["_out"], c["_used"]
        start, end = t, t + d

        # video clipitem (вложенного <id> в родном FCP7 нет — только атрибут)
        vi = ET.SubElement(vtrack, "clipitem", id=f"clipitem-video-{i}")
        ET.SubElement(vi, "name").text = c["file"]
        ET.SubElement(vi, "enabled").text = "TRUE"
        ET.SubElement(vi, "duration").text = str(d)
        vi.append(_rate_el(fps))
        ET.SubElement(vi, "start").text = str(start)
        ET.SubElement(vi, "end").text = str(end)
        ET.SubElement(vi, "in").text = str(src_in)
        ET.SubElement(vi, "out").text = str(src_out)
        ET.SubElement(vi, "alphatype").text = "none"
        ET.SubElement(vi, "pixelaspectratio").text = "square"
        ET.SubElement(vi, "anamorphic").text = "FALSE"

        vi.append(_video_file_el(f"file-{i}v", c, orig, fps, width, height))

        # link video<->audio
        link = ET.SubElement(vi, "link")
        ET.SubElement(link, "linkclipref").text = f"clipitem-audio-{i}"
        ET.SubElement(link, "mediatype").text = "video"
        ET.SubElement(link, "trackindex").text = "1"
        ET.SubElement(link, "clipindex").text = "1"

        # надёжность: комментарий с планом виден в Premiere как label? кладём в lognote
        note = f"[{c['plan']}|{c['hero']}] {c['desc']}"
        if trim_rec and (src_out - src_in) < orig:
            note += f" [рыба: взято {src_out - src_in}/{orig}к из середины]"
        ET.SubElement(vi, "lognote").text = note

        # audio clipitem: звук либо из видеофайла, либо из отдельного
        # микрофона (колонка audio из interview.py) — тогда в A1 сразу чистый звук
        ai = ET.SubElement(atrack, "clipitem", id=f"clipitem-audio-{i}")
        if c.get("audio"):
            aname = Path(c["audio"]).name
            aabspath = c["audio"]
            if not Path(aabspath).is_absolute():
                aabspath = str(media_dir_global / aabspath)
        else:
            aname, aabspath = c["file"], c["abspath"]
        ET.SubElement(ai, "name").text = aname
        ET.SubElement(ai, "enabled").text = "TRUE"
        ET.SubElement(ai, "duration").text = str(d)
        ai.append(_rate_el(fps))
        ET.SubElement(ai, "start").text = str(start)
        ET.SubElement(ai, "end").text = str(end)
        ET.SubElement(ai, "in").text = str(src_in)
        ET.SubElement(ai, "out").text = str(src_out)
        ai.append(_audio_file_el(f"file-{i}a",
                                 {"file": aname, "abspath": aabspath},
                                 max(orig, src_out), fps))
        ET.SubElement(ai, "sourcetrack").append(_audio_track_el())
        link2 = ET.SubElement(ai, "link")
        ET.SubElement(link2, "linkclipref").text = f"clipitem-video-{i}"
        ET.SubElement(link2, "mediatype").text = "audio"
        ET.SubElement(link2, "trackindex").text = "1"
        ET.SubElement(link2, "clipindex").text = "1"

        t = end

    for k, (tr, afile) in enumerate(zip(extra_tracks, audios)):
        mc = {"file": Path(afile).name, "abspath": str(Path(afile).absolute())}
        mi = ET.SubElement(tr, "clipitem", id=f"clipitem-ax{k}")
        ET.SubElement(mi, "name").text = Path(afile).name
        ET.SubElement(mi, "enabled").text = "TRUE"
        ET.SubElement(mi, "duration").text = str(total)
        mi.append(_rate_el(fps))
        ET.SubElement(mi, "start").text = "0"
        ET.SubElement(mi, "end").text = str(total)
        ET.SubElement(mi, "in").text = "0"
        ET.SubElement(mi, "out").text = str(total)
        mi.append(_audio_file_el(f"file-ax{k}", mc, total, fps))
        ET.SubElement(mi, "sourcetrack").append(_audio_track_el())

    return xmeml


def _rate_el(fps: int) -> ET.Element:
    r = ET.Element("rate")
    ET.SubElement(r, "timebase").text = str(fps)
    ET.SubElement(r, "ntsc").text = "FALSE"
    return r


def _audio_track_el() -> ET.Element:
    st = ET.Element("sourcetrack")
    ET.SubElement(st, "mediatype").text = "audio"
    ET.SubElement(st, "trackindex").text = "1"
    return st


def check_xml(root: ET.Element) -> List[str]:
    """Самопроверка перед записью: дубли ID и висячие ссылки роняют импорт в Premiere."""
    errs: List[str] = []
    ids = [e.get("id") for e in root.iter() if e.get("id")]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        errs.append(f"дубли id: {dupes}")
    refs = {l.text for l in root.iter("linkclipref")}
    if not refs <= set(ids):
        errs.append(f"висячие ссылки: {sorted(refs - set(ids))}")
    n_v = len(root.findall(".//video/track/clipitem"))
    n_a = len(root.findall(".//audio/track/clipitem"))
    if n_v == 0:
        errs.append("нет видео-клипов")
    return errs


def write_pretty_xml(root: ET.Element, path: Path):
    raw = ET.tostring(root, encoding="utf-8")
    pretty = minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8")
    path.write_bytes(pretty)


def write_edl(clips: List[Dict], path: Path, fps: int = 25, trim_rec: bool = False):
    lines = [f"TITLE: {path.stem}", f"FCM: NON-DROP FRAME", ""]
    rec = 0
    for i, c in enumerate(clips, 1):
        if "_used" in c:
            orig, src_in, src_out, d = c["_orig"], c["_in"], c["_out"], c["_used"]
        else:
            orig, src_in, src_out, d = take_window(c, fps, trim_rec)
        rec_in, rec_out = rec, rec + d
        lines.append(
            f"{i:03d}  AX       V     C        {tc(src_in, fps)} {tc(src_out, fps)} {tc(rec_in, fps)} {tc(rec_out, fps)}"
        )
        lines.append(f"* FROM CLIP NAME: {c['file']} [{c['plan']}|{c['hero']}]")
        rec += d
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AutoCut Premiere — CSV -> XML/EDL для Premiere Pro")
    ap.add_argument("--timeline", required=True, help="CSV таймлайна (из autocut --out)")
    ap.add_argument("--media-dir", default=".", help="папка с исходниками (если в CSV нет path)")
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--name", default="AutoCut", help="имя секвенции")
    ap.add_argument("--out", default="premiere.xml", help="выходной XML")
    ap.add_argument("--edl", default="premiere.edl", help="выходной EDL (пусто = не писать)")
    ap.add_argument("--trim-rec", action="store_true",
                    help="РЫБА: урезать каждый кусок до верха нормы плана "
                         "(общий 8с, средний 5-6с, крупный 4с, деталь 3с), брать из середины")
    ap.add_argument("--music", default="", help="готовая подложка (wav из music.py) на дорожку A2")
    ap.add_argument("--audio", nargs="*", default=[],
                    help="целые аудиодорожки (микрофоны): --audio гость.wav ведущий.wav")
    args = ap.parse_args(argv)

    tl = Path(args.timeline)
    if not tl.is_file():
        print(f"Не найден таймлайн: {tl}")
        print("Сначала собери его: autocut.py --out timeline.csv или interview.py (selects.csv).")
        return 2
    if args.music and not Path(args.music).is_file():
        print(f"Не найден файл музыки: {args.music} (сначала music.py). Продолжаю без подложки.")
        args.music = ""
    audios = [a for a in (args.audio or []) if a]
    for a in list(audios):
        if not Path(a).is_file():
            print(f"Нет аудиофайла: {a} — пропускаю.")
            audios.remove(a)
    if args.music:
        audios.append(args.music)
    clips = load_timeline(tl)
    if not clips:
        print(f"В {tl} нет строк.")
        return 1
    clips = resolve_media(clips, Path(args.media_dir))
    missing = [c for c in clips if not c["exists"]]
    if missing:
        print(f"Внимание: {len(missing)} файлов не найдено на диске. Premiere попросит relink — это нормально.")
        for c in missing[:5]:
            print(f"  нет: {c['abspath']}")

    root = build_fcp7(clips, fps=args.fps, width=args.width, height=args.height,
                      seq_name=args.name, trim_rec=args.trim_rec, audios=audios)
    problems = check_xml(root)
    if problems:
        print("XML не записан — структура битая (это баг, пришли мне эти строки):")
        for p in problems:
            print("  ! " + p)
        return 3
    write_pretty_xml(root, Path(args.out))
    print(f"XML сохранён: {args.out} ({len(clips)} клипов, fps {args.fps}). Импорт: Premiere > File > Import.")
    if args.edl:
        write_edl(clips, Path(args.edl), fps=args.fps, trim_rec=args.trim_rec)
        print(f"EDL сохранён: {args.edl} (запасной вариант).")
    total_s = sum(c["_used"] for c in clips) / args.fps
    raw_s = sum(c["_orig"] for c in clips) / args.fps
    if args.trim_rec:
        print(f"РЫБА: {raw_s:.1f}с исходников -> {total_s:.1f}с в секвенции. "
              f"Порядок проверен autocut, длины — по нормам плана.")
    else:
        print(f"Хронометраж: {total_s:.1f}с. Порядок уже проверен autocut по склейкам.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

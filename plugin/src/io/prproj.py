# -*- coding: utf-8 -*-
"""Чтение проекта Premiere (.prproj): секвенции, дорожки, клипы.

Зачем это нужно: проект Premiere — это и есть «как должно быть».
Прежде чем придумывать сборку, проще прочитать чужую и сравнить.

Файл .prproj — gzip, внутри XML. Устройство:

  PremiereData
    Sequence                        секвенция, имя в <Name>
      TrackGroups/TrackGroup          Second -> группа дорожек (ObjectID)
      PersistentGroupContainer
        LinkContainer/Links/Link      ссылка: один клип, видео + звук (линк)
          TrackItemGroup/TrackItems/TrackItem -> элемент дорожки

    VideoTrackGroup / AudioTrackGroup
      TrackGroup/FrameRect            разрешение
      TrackGroup/FrameRate            тиков в секунду на кадр
      TrackGroup/Tracks/Track         дорожка (Index, ObjectURef)

    VideoClipTrack / AudioClipTrack   дорожка
      ClipTrack/ClipItems/TrackItems/TrackItem -> элемент дорожки

    VideoClipTrackItem / AudioClipTrackItem
      ClipTrackItem/TrackItem/Start,End   положение на таймлайне (тики)
      ClipTrackItem/SubClip               клип источника (или ObjectRef)
        Clip -> VideoClip -> Clip -> InPoint, OutPoint, Source
          Source -> Media -> FilePath

Время в Premiere измеряется тиками: TICKS_PER_SECOND = 254016000000.
Частота кадров = TICKS_PER_SECOND / FrameRate.

**Start может отсутствовать.** Premiere не пишет начало, если клип идёт
вплотную за предыдущим на той же дорожке. Тогда начало = конец
предыдущего клипа дорожки.
"""
from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

TICKS = 254016000000.0
VIDEO_EXT = (".mp4", ".mov", ".mxf", ".avi", ".mts")


class Clip:
    """Один клип на дорожке."""

    def __init__(self, track, kind, start, end, name, src_in, src_out, path):
        self.track = track
        self.kind = kind
        self.start = start
        self.end = end
        self.name = name
        self.src_in = src_in
        self.src_out = src_out
        self.path = path

    @property
    def dur(self):
        return max(0.0, self.end - self.start)

    def __repr__(self):
        return "<{} {} t={:.2f} {:.2f}s src={:.2f}..{:.2f}>".format(
            self.kind, self.name, self.start, self.dur, self.src_in, self.src_out)


class Project:
    """Разобранный проект Premiere."""

    def __init__(self, path):
        raw = Path(path).read_bytes()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        self.root = ET.fromstring(raw)
        self.by_id = {}
        self.by_uid = {}
        for c in self.root.iter():
            oid = c.get("ObjectID")
            if oid and oid not in self.by_id:
                self.by_id[oid] = c
            uid = c.get("ObjectUID")
            if uid and uid not in self.by_uid:
                self.by_uid[uid] = c

    @staticmethod
    def _t(v):
        try:
            return int(v)
        except Exception:
            return 0

    def _clip(self, item, track_no):
        cti = item.find("ClipTrackItem")
        ti = cti.find("TrackItem") if cti is not None else None
        start = self._t(ti.findtext("Start")) if ti is not None else 0
        end = self._t(ti.findtext("End")) if ti is not None else 0
        has_start = ti is not None and ti.findtext("Start") is not None
        sub = item.find("ClipTrackItem/SubClip")
        if sub is not None and sub.get("ObjectRef"):
            sub = self.by_id.get(sub.get("ObjectRef"))
        name, src_in, src_out, path = "", 0.0, 0.0, ""
        if sub is not None:
            name = (sub.findtext("Name") or "").strip()
            ref = sub.find("Clip")
            clip = self.by_id.get(ref.get("ObjectRef")) if ref is not None else None
            if clip is not None:
                holder = clip
                if holder.find("InPoint") is None and holder.find("Clip") is not None:
                    holder = holder.find("Clip")
                src_in = self._t(holder.findtext("InPoint")) / TICKS
                src_out = self._t(holder.findtext("OutPoint")) / TICKS
                src = holder.find("Source")
                if src is not None:
                    s = self.by_id.get(src.get("ObjectRef"))
                    if s is not None:
                        media = s.find("Media")
                        if media is None:
                            # у источника медиа лежит внутри MediaSource
                            ms = s.find("MediaSource")
                            media = ms.find("Media") if ms is not None else None
                        if media is not None:
                            # Media бывает и по ObjectRef, и по
                            # ObjectURef — смотря откуда ссылка
                            m = (self.by_id.get(media.get("ObjectRef"))
                                 or self.by_uid.get(media.get("ObjectURef")))
                            if m is not None:
                                path = m.findtext("FilePath") or ""
        kind = "video" if item.tag == "VideoClipTrackItem" else "audio"
        c = Clip(track_no, kind, start / TICKS, end / TICKS, name,
                 src_in, src_out, path)
        c.has_start = has_start
        return c

    def _group(self, seq, tag):
        tg = seq.find("TrackGroups")
        if tg is None:
            return None
        for grp in tg.findall("TrackGroup"):
            sec = grp.find("Second")
            g = self.by_id.get(sec.get("ObjectRef")) if sec is not None else None
            if g is not None and g.tag == tag:
                return g
        return None

    def _tracks(self, group, kind):
        """Дорожки группы: [(номер, [клипы])] в порядке Index."""
        out = []
        if group is None:
            return out
        tag = "VideoClipTrack" if kind == "video" else "AudioClipTrack"
        item_tag = ("VideoClipTrackItem" if kind == "video"
                    else "AudioClipTrackItem")
        for tr in group.findall("TrackGroup/Tracks/Track"):
            track = self.by_uid.get(tr.get("ObjectURef"))
            if track is None or track.tag != tag:
                continue
            idx = int(tr.get("Index") or 0)
            items = track.find("ClipTrack/ClipItems/TrackItems")
            clips = []
            if items is not None:
                for ti in items.findall("TrackItem"):
                    it = self.by_id.get(ti.get("ObjectRef"))
                    if it is not None and it.tag == item_tag:
                        clips.append(self._clip(it, idx + 1))
            # Нет Start — клип идёт вплотную за предыдущим.
            for i, c in enumerate(clips):
                if not getattr(c, "has_start", True):
                    c.start = clips[i - 1].end if i else 0.0
            out.append((idx + 1, clips))
        return out

    def sequences(self):
        """Секвенции проекта: словари с дорожками и параметрами."""
        for seq in self.root.findall("Sequence"):
            vg = self._group(seq, "VideoTrackGroup")
            ag = self._group(seq, "AudioTrackGroup")
            fps = 25.0
            rect = ""
            if vg is not None:
                rect = vg.findtext("FrameRect") or ""
                fr = vg.findtext("TrackGroup/FrameRate")
                if fr:
                    fps = TICKS / float(fr)
            yield {
                "name": (seq.findtext("Name") or "").strip(),
                "fps": fps,
                "rect": rect,
                "video": self._tracks(vg, "video"),
                "audio": self._tracks(ag, "audio"),
            }


def main(argv=None):
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    path = Path(argv[0]) if argv else Path(
        r"E:\домашка дайджест СДЕЛАННО\дайджест домашка.prproj")
    p = Project(path)
    print("ПРОЕКТ:", path.name)
    for s in p.sequences():
        vclips = [c for _n, clips in s["video"] for c in clips]
        aclips = [c for _n, clips in s["audio"] for c in clips]
        if not vclips and not aclips:
            continue
        print()
        print("=== «{}»  {}  {} fps  дорожек V{} A{}  клипов {} / {}".format(
            s["name"], s["rect"], round(s["fps"], 3),
            len(s["video"]), len(s["audio"]), len(vclips), len(aclips)))
        for n, clips in s["video"]:
            if not clips:
                continue
            print("  V{}: {} клипов, {:.1f}..{:.1f}с".format(
                n, len(clips), min(c.start for c in clips),
                max(c.end for c in clips)))
            for c in clips:
                print("     t={:7.2f} {:>6.2f}с {:<14} src {:8.2f}..{:8.2f}".format(
                    c.start, c.dur, c.name[:14], c.src_in, c.src_out))
        for n, clips in s["audio"]:
            if not clips:
                continue
            print("  A{}: {} клипов, {:.1f}..{:.1f}с".format(
                n, len(clips), min(c.start for c in clips),
                max(c.end for c in clips)))
            for c in clips:
                print("     t={:7.2f} {:>6.2f}с {:<14} src {:8.2f}..{:8.2f}".format(
                    c.start, c.dur, c.name[:14], c.src_in, c.src_out))


if __name__ == "__main__":
    main()

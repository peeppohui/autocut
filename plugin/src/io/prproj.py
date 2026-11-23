# -*- coding: utf-8 -*-
"""Разбор готового проекта Premiere (.prproj) - «эталон» для сборки.

.prproj - это XML, сжатый gzip. Объекты ссылаются друг на друга по
ObjectID/ObjectRef (внутри файла) и ObjectUID/ObjectURef (между
крупными объектами). Цепочка от секвенции до файла на диске:

    Sequence -> TrackGroups -> (Video|Audio)TrackGroup -> Tracks
      -> (Video|Audio)ClipTrack -> ClipItems -> TrackItem
      -> (Video|Audio)ClipTrackItem -> SubClip -> Clip
      -> (Video|Audio)MediaSource -> Media -> FilePath

Модуль только читает проект и ничего не пишет. Он нужен, чтобы снять
с готового ролика рецепт (доли частей, длины кусков) и сверить его с
тем, что собрал движок.

Единицы: тики Premiere, 254016000000 в секунде.
"""
from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

TICKS = 254016000000

# части ролика определяются по имени папки с исходником
PART_BY_FOLDER = (
    ("стендап", "standup"),
    ("интервью", "interview"),
    ("нарезка", "broll"),
    ("звк", "recorder"),
)
MUSIC_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac"}


@dataclass
class Item:
    """Один клип на дорожке секвенции."""
    kind: str            # "video" | "audio"
    track: int           # номер дорожки с нуля
    start: float         # начало на таймлайне, с
    end: float           # конец на таймлайне, с
    src_in: float        # точка входа в исходнике, с
    src_out: float       # точка выхода в исходнике, с
    name: str
    path: str            # путь к файлу ('' если не нашли)
    part: str            # standup | interview | broll | recorder | music | other

    @property
    def dur(self) -> float:
        return self.end - self.start


@dataclass
class Sequence:
    name: str
    items: List[Item] = field(default_factory=list)

    def of(self, kind: str) -> List[Item]:
        return [i for i in self.items if i.kind == kind]

    @property
    def duration(self) -> float:
        return max((i.end for i in self.items), default=0.0)


@dataclass
class Project:
    sequences: List[Sequence]
    markers: List[str]

    def sequence(self, name: str) -> Optional[Sequence]:
        """Секвенция по имени. Если их несколько - самая наполненная."""
        same = [s for s in self.sequences if s.name == name]
        return max(same, key=lambda s: len(s.items)) if same else None


def _read_xml(path: Path) -> ET.Element:
    raw = Path(path).read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return ET.fromstring(raw)


def classify(path: str) -> str:
    """Часть ролика по пути исходника."""
    low = path.replace("\\", "/").lower()
    folders = low.split("/")[:-1]
    for key, part in PART_BY_FOLDER:
        if any(f == key for f in folders):
            return part
    if Path(low).suffix in MUSIC_EXT:
        return "music"
    return "other"


def _num(el: Optional[ET.Element], default: float = 0.0) -> float:
    try:
        return float(el.text) if el is not None and el.text else default
    except ValueError:
        return default


def parse(path) -> Project:
    root = _read_xml(Path(path))
    byid: Dict[str, ET.Element] = {}
    byuid: Dict[str, ET.Element] = {}
    for e in root.iter():
        if e.get("ObjectID"):
            byid[e.get("ObjectID")] = e
        if e.get("ObjectUID"):
            byuid[e.get("ObjectUID")] = e

    def ref(el: Optional[ET.Element]) -> Optional[ET.Element]:
        if el is None:
            return None
        if el.get("ObjectRef"):
            return byid.get(el.get("ObjectRef"))
        if el.get("ObjectURef"):
            return byuid.get(el.get("ObjectURef"))
        return None

    def file_of(clip_obj: ET.Element) -> str:
        """VideoClip/AudioClip -> путь к файлу."""
        src = ref(clip_obj.find("./Clip/Source"))
        media = ref(src.find("./MediaSource/Media")) if src is not None else None
        if media is None:
            return ""
        for tag in ("ActualMediaFilePath", "FilePath"):
            t = media.findtext(tag)
            if t:
                return t
        return ""

    def items_of(track: ET.Element, kind: str, idx: int) -> List[Item]:
        out: List[Item] = []
        for ti in track.findall(".//ClipItems//TrackItem"):
            cti = ref(ti)
            if cti is None:
                continue
            se = cti.find("./ClipTrackItem/TrackItem")
            if se is None:
                continue
            start = _num(se.find("Start")) / TICKS
            end = _num(se.find("End")) / TICKS
            sub = ref(cti.find("./ClipTrackItem/SubClip"))
            clip = ref(sub.find("Clip")) if sub is not None else None
            name = (sub.findtext("Name") if sub is not None else "") or ""
            path_ = file_of(clip) if clip is not None else ""
            cin = cout = 0.0
            if clip is not None:
                cin = _num(clip.find("./Clip/InPoint")) / TICKS
                cout = _num(clip.find("./Clip/OutPoint")) / TICKS
            out.append(Item(kind, idx, start, end, cin, cout, name, path_,
                            classify(path_) if path_ else "other"))
        return out

    sequences: List[Sequence] = []
    for seq in byuid.values():
        if seq.tag != "Sequence":
            continue
        s = Sequence(seq.findtext("Name") or "")
        for tg in seq.findall("./TrackGroups/TrackGroup"):
            grp = ref(tg.find("Second"))
            if grp is None:
                continue
            kind = "video" if grp.tag.startswith("Video") else (
                "audio" if grp.tag.startswith("Audio") else "")
            if not kind:
                continue
            for t in grp.findall("./TrackGroup/Tracks/Track"):
                track = ref(t)
                if track is None:
                    continue
                s.items += items_of(track, kind, int(t.get("Index", "0")))
        sequences.append(s)

    markers: List[str] = []
    for m in root.iter("DVAMarker"):
        txt = m.text or ""
        i = txt.find('"mComment":"')
        if i >= 0:
            j = txt.find('","mMarkerID"', i)
            markers.append(txt[i + 12:j if j > 0 else None])
    return Project(sequences, markers)


def shares(seq: Sequence) -> Dict[str, float]:
    """Доли частей в видеоряде, 0..1 (по длительности клипов на V-дорожках).

    Дорожки считаются по отдельности: перебивка поверх интервью
    считается и за интервью, и за нарезку, поэтому сумма может быть
    больше единицы. Для рецепта это то, что надо: сколько экрана
    занимает каждая часть.
    """
    total = seq.duration or 1.0
    acc: Dict[str, float] = defaultdict(float)
    for it in seq.of("video"):
        acc[it.part] += it.dur
    return {k: round(v / total, 3) for k, v in sorted(acc.items())}


def summary(seq: Sequence) -> dict:
    """Короткая сводка по секвенции: для сверки с планом движка."""
    v = [i for i in seq.of("video") if i.part == "broll"]
    d = sorted(i.dur for i in v)
    return {
        "name": seq.name,
        "duration_sec": round(seq.duration, 1),
        "video_items": len(seq.of("video")),
        "audio_items": len(seq.of("audio")),
        "shares": shares(seq),
        "broll_median_sec": round(d[len(d) // 2], 2) if d else 0.0,
        "broll_min_sec": round(d[0], 2) if d else 0.0,
        "broll_max_sec": round(d[-1], 2) if d else 0.0,
    }


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 2:
        print("usage: prproj.py project.prproj [sequence]")
        sys.exit(2)
    pr = parse(sys.argv[1])
    names = sys.argv[2:] or [s.name for s in pr.sequences]
    for n in dict.fromkeys(names):
        sq = pr.sequence(n)
        if sq:
            print(json.dumps(summary(sq), ensure_ascii=False, indent=2))

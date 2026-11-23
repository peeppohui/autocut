# -*- coding: utf-8 -*-
"""Разбор .prproj: цепочка Sequence -> файл на диске и доли частей.

Проект собирается синтетически и сжимается gzip, как настоящий:
реальный проект в репозиторий не кладём (он большой и чужой).
"""
import gzip
import sys
import tempfile
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
import bootstrap  # noqa: F401,F403

import prproj as P

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


T = P.TICKS


def media(uid, path):
    return (f'<Media ObjectUID="{uid}"><FilePath>{path}</FilePath>'
            f'<ActualMediaFilePath>{path}</ActualMediaFilePath></Media>')


def chain(n, uid, path, name, tin, tout, start, end):
    """Один клип: TrackItem -> SubClip -> VideoClip -> Source -> Media."""
    return (
        f'<VideoClipTrackItem ObjectID="{n}"><ClipTrackItem>'
        f'<TrackItem><Start>{int(start*T)}</Start><End>{int(end*T)}</End></TrackItem>'
        f'<SubClip ObjectRef="{n+1}"/></ClipTrackItem></VideoClipTrackItem>'
        f'<SubClip ObjectID="{n+1}"><Clip ObjectRef="{n+2}"/><Name>{name}</Name></SubClip>'
        f'<VideoClip ObjectID="{n+2}"><Clip><Source ObjectRef="{n+3}"/>'
        f'<InPoint>{int(tin*T)}</InPoint><OutPoint>{int(tout*T)}</OutPoint></Clip></VideoClip>'
        f'<VideoMediaSource ObjectID="{n+3}"><MediaSource><Media ObjectURef="{uid}"/>'
        f'</MediaSource></VideoMediaSource>')


XML = (
    '<?xml version="1.0" encoding="UTF-8" ?><PremiereData Version="3">'
    + media("m-stand", "E:\\x\\материал\\исх\\стендап\\C0001.MP4")
    + media("m-brol", "E:\\x\\материал\\исх\\нарезка\\C0002.MP4")
    + media("m-mus", "E:\\x\\music.mp3")
    + chain(10, "m-stand", "", "C0001.MP4", 5, 25, 0, 20)
    + chain(20, "m-brol", "", "C0002.MP4", 1, 3, 20, 22)
    + chain(30, "m-brol", "", "C0002.MP4", 4, 6.5, 22, 24.5)
    + '<VideoClipTrack ObjectUID="t0"><ClipTrack><ClipItems>'
      '<TrackItems><TrackItem ObjectRef="10"/></TrackItems></ClipItems></ClipTrack></VideoClipTrack>'
    + '<VideoClipTrack ObjectUID="t1"><ClipTrack><ClipItems>'
      '<TrackItems><TrackItem ObjectRef="20"/><TrackItem ObjectRef="30"/></TrackItems>'
      '</ClipItems></ClipTrack></VideoClipTrack>'
    + '<VideoTrackGroup ObjectID="50"><TrackGroup><Tracks>'
      '<Track Index="0" ObjectURef="t0"/><Track Index="1" ObjectURef="t1"/>'
      '</Tracks></TrackGroup></VideoTrackGroup>'
    + '<Sequence ObjectUID="s1"><Name>Тест</Name><TrackGroups>'
      '<TrackGroup Index="0"><First>x</First><Second ObjectRef="50"/></TrackGroup>'
      '</TrackGroups></Sequence>'
    + '<Marker ObjectID="90"><DVAMarker>{"DVAMarker":{"mComment":"привет, мир",'
      '"mMarkerID":"a","mName":"- / -"}}</DVAMarker></Marker>'
    + '</PremiereData>')

# в синтетике путь берётся из Media: подставим его через chain с пустым path,
# поэтому Media должна быть найдена по ObjectURef
with tempfile.TemporaryDirectory() as td:
    f = _P(td) / "t.prproj"
    f.write_bytes(gzip.compress(XML.encode("utf-8")))
    pr = P.parse(f)
    sq = pr.sequence("Тест")
    ok("секвенция найдена", sq is not None)
    items = sq.of("video") if sq else []
    ok("три клипа на видео", len(items) == 3, str(len(items)))
    by = {i.part for i in items}
    ok("части определены по папкам", by == {"standup", "broll"}, str(by))
    st = [i for i in items if i.part == "standup"][0]
    ok("тайминги таймлайна", (st.start, st.end) == (0.0, 20.0), f"{st.start} {st.end}")
    ok("точки входа и выхода в исходнике",
       (st.src_in, st.src_out) == (5.0, 25.0), f"{st.src_in} {st.src_out}")
    ok("длительность секвенции", abs(sq.duration - 24.5) < 1e-6, str(sq.duration))
    sh = P.shares(sq)
    ok("доли частей", abs(sh["standup"] - 0.816) < 0.01 and abs(sh["broll"] - 0.184) < 0.01, str(sh))
    s = P.summary(sq)
    ok("медиана нарезки", s["broll_median_sec"] in (2.0, 2.5), str(s))
    ok("маркер прочитан", pr.markers == ["привет, мир"], str(pr.markers))

ok("классификация: стендап", P.classify("E:\\a\\стендап\\x.mp4") == "standup")
ok("классификация: звук рекордера", P.classify("E:/a/звк/REC1.wav") == "recorder")
ok("классификация: музыка", P.classify("E:\\a\\song.mp3") == "music")
ok("классификация: прочее", P.classify("E:\\a\\clip.mp4") == "other")
ok("имя папки целиком, не подстрока", P.classify("E:\\a\\интервьюшки\\x.mp4") == "other")

if fail:
    print("ПАДЕНИЕ:", ", ".join(fail))
    sys.exit(1)
print("PRPROJ OK")

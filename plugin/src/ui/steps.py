# -*- coding: utf-8 -*-
"""Сборка командных строк шагов.

Отдельный модуль, потому что страницы нуждаются в этом и из app.py,
и из pages_news.py. Если держать такие функции в app.py, то страница
пришла бы импортировать app, а app импортирует страницу - круговая
ссылка, которая в Python разрешается через sys.modules и потом
всплывает как «AttributeError в середине обработчика кнопки».

Здесь же живёт единственное место, где решается, писать ли EDL и
резать ли куски по плану: раньше эти два флага молча передавались из
шести мест, и настройки export.write_edl и export.trim_to_plan не
влияли ни на что.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent        # plugin/src/ui
SRC = HERE.parent                            # plugin/src
IO = SRC / "io"
ML = SRC / "ml"
MODES = SRC / "modes"

sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "config"))

import settings                              # noqa: E402


def premiere_args(timeline, project, stacked=False, music=None):
    """Аргументы сборки XML для Premiere."""
    p = Path(project)
    argv = [str(IO / "premiere.py"), "--timeline", str(timeline),
            "--media-dir", str(p), "--out", str(p / "ryba.xml")]
    if bool(settings.get("export.write_edl", True)):
        argv += ["--edl", str(p / "ryba.edl")]
    if bool(settings.get("export.trim_to_plan", True)):
        argv += ["--trim-rec"]
    if stacked:
        argv.append("--stacked")
    if music:
        argv += ["--music", str(music)]
    return argv


def srt_args(timeline, project):
    return [str(IO / "srt.py"), "--selects", str(timeline),
            "--out", str(Path(project) / "ryba.srt")]


def sync_args(ref, project, cam="", mic_guest="", mic_host=""):
    """Аргументы синхронизации. Пустые дорожки просто не передаются."""
    argv = [str(ML / "sync.py"), "--ref", ref,
            "--outdir", str(Path(project) / "synced")]
    if cam:
        argv += ["--cam", cam]
    if mic_guest:
        argv += ["--mic-guest", mic_guest]
    if mic_host:
        argv += ["--mic-host", mic_host]
    return argv
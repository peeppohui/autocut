# -*- coding: utf-8 -*-
"""Разбор папки с материалом.

Панель спрашивает движок «что в папке», а не гадает по именам файлов.
Здесь та же логика, что была в `mcp_server.tools`, но без MCP: движок
не должен зависеть от временного транспорта.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
for _path in (str(SRC), str(SRC / "config")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bootstrap  # noqa: E402,F401

VIDEO_EXT = (".mp4", ".mov", ".mxf", ".avi", ".mts", ".mp4v")


def _videos(folder: Path):
    if not folder or not Path(folder).is_dir():
        return []
    return sorted(p for p in Path(folder).iterdir()
                  if p.suffix.lower() in VIDEO_EXT)


def _duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def material(folder: str) -> dict:
    """Что лежит в папке: три части, сколько файлов, сколько секунд.

    `sorter` ищет материал во вложенной папке (`исх`, `исходники`,
    `видео`...) и говорит, если нашёл несколько кандидатов. Здесь та же
    логика: монтажёр должен видеть, где материал, а не угадывать.
    """
    from ml import sorter
    base = Path(folder).expanduser()
    if not base.is_dir():
        return {"ok": False, "error": "нет папки: " + str(base)}
    root, hint = sorter.find_materials(base)
    if root is None:
        return {"ok": False, "error": hint or
                ("в папке не нашлось материала: " + str(base))}
    from modes import digest
    parts = digest.resolve_parts(root)
    names = sorter.part_names()
    out = {"ok": True, "папка": str(base), "материал": str(root),
           "подсказка": hint or "", "части": {}}
    for key, folder_path in parts.items():
        files = _videos(folder_path)
        secs = sum(_duration(p) for p in files)
        out["части"][names.get(key, key)] = {
            "папка": str(folder_path), "файлов": len(files),
            "секунд": round(secs, 1),
            "примеры": [p.name for p in files[:5]]}
    return out

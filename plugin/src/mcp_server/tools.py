# -*- coding: utf-8 -*-
"""Операции AutoCut, доступные агенту.

Отдельно от MCP-сервера: здесь нет ни одного упоминания протокола.
Операции — обычные функции, их можно звать и из окна, и из теста, и
из агента. MCP-обвязка живёт в `server.py` и только переводит вызовы
в эти функции.
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SRC / "config") not in sys.path:
    sys.path.insert(0, str(SRC / "config"))

import bootstrap            # noqa: E402,F401

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
    логика: агент должен видеть, где материал, а не угадывать.
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


def read_csv(path: str, limit: int = 0) -> dict:
    """Прочитать CSV и отдать строки словарями."""
    p = Path(path).expanduser()
    if not p.is_file():
        return {"ok": False, "error": "нет файла: " + str(p)}
    with p.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if limit and len(rows) > limit:
        rows = rows[:limit]
    return {"ok": True, "файл": str(p), "строк": len(rows), "строки": rows}


def build_xml(timeline: str, out: str, media_dir: str = "", name: str = "",
              edl: str = "", fps: int = 25) -> dict:
    """Собрать FCP7 XML и EDL из плана.

    Собранный XML Premiere импортирует: проверено на Premiere Pro 2026
    26.3.2.2 (лестница в AGENTS.md). Файлы в плане — относительно
    `media_dir`, поэтому media_dir должен быть тем же корнем, который
    человек указал в окне.
    """
    tl = Path(timeline).expanduser()
    if not tl.is_file():
        return {"ok": False, "error": "нет плана: " + str(tl)}
    outp = Path(out).expanduser()
    outp.parent.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, "-u", str(SRC / "io" / "premiere.py"),
            "--timeline", str(tl), "--out", str(outp),
            "--fps", str(fps)]
    if media_dir:
        argv += ["--media-dir", str(Path(media_dir).expanduser())]
    if name:
        argv += ["--name", name]
    if edl:
        argv += ["--edl", str(Path(edl).expanduser())]
    r = subprocess.run(argv, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    tail = [ln for ln in (r.stdout or "").splitlines() if ln.strip()][-6:]
    return {"ok": r.returncode == 0, "код": r.returncode,
            "xml": str(outp) if outp.is_file() else "",
            "журнал": tail,
            "ошибка": (r.stderr or "")[-800:] if r.returncode else ""}


def settings_get(prefix: str = "") -> dict:
    """Настройки AutoCut (или одну секцию)."""
    import settings as S
    all_s = S.all() if hasattr(S, "all") else {}
    if not all_s:
        # у settings нет all(): читаем файл напрямую
        cfg = json.loads((SRC / "config" / "settings.json").read_text(
            encoding="utf-8"))

        def walk(node, path=""):
            for k, v in node.items():
                p = path + k
                if isinstance(v, dict) and not k.startswith("_"):
                    # yield from, а не просто вызов: без него вложенные
                    # секции молча теряются, и настроек не видно.
                    yield from walk(v, p + ".")
                elif not k.startswith("_"):
                    yield p, v
        all_s = dict(walk(cfg))
    if prefix:
        pref = prefix if prefix.endswith(".") else prefix + "."
        all_s = {k: v for k, v in all_s.items() if k.startswith(pref)}
    return {"ok": True, "настройки": all_s}


def settings_set(key: str, value) -> dict:
    """Записать настройку. Значение — как в settings.json."""
    import settings as S
    try:
        S.set_value(key, value)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "ключ": key, "значение": S.get(key, None)}

"""Подключает все модульные папки AutoCut в sys.path.

Модули лежат в src/core, src/modes, src/io, src/ml — из соседних папок они
не видны друг другу по имени. Этот файл добавляет каждую папку в путь, после
чего обычные `import gpu`, `from sync import find_offset`, `from autocut import
Clip` продолжают работать без единого изменения в вызывающем коде.

Импортируется из модулей, которым нужен соседний модуль.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent

SUBDIRS = ("core", "modes", "io", "ml", "config")


def install() -> None:
    """Добавляет корень src и все его модульные папки в sys.path."""
    for path in (SRC, *(SRC / name for name in SUBDIRS)):
        p = str(path)
        if path.is_dir() and p not in sys.path:
            sys.path.append(p)


def install_ffmpeg() -> None:
    """Добавляет .tools/ffmpeg/*/bin в начало PATH этого процесса.

    Панель стартует движок из Premiere, и PATH у того процесса —
    премьеровский: ffmpeg/ffprobe в нём нет. Распознавание тогда шло,
    а первый же subprocess.run(["ffprobe", ...]) падал с
    FileNotFoundError и вся сборка вставала. Правка PATH при импорте
    чинит все запуски сразу: воркеры задач движка (каждый — отдельный
    процесс) и ручные из консоли.
    """
    cur = SRC
    root = cur
    for _ in range(3):
        if (cur / ".tools").is_dir():
            root = cur
            break
        cur = cur.parent
    for bin_dir in (root / ".tools").glob("ffmpeg/*/bin"):
        if ((bin_dir / "ffprobe.exe").exists()
                and str(bin_dir) not in os.environ.get("PATH", "")):
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get(
                "PATH", "")


install()
install_ffmpeg()
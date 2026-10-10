"""Подключает все модульные папки AutoCut в sys.path.

Модули лежат в src/core, src/modes, src/io, src/ml — из соседних папок они
не видны друг другу по имени. Этот файл добавляет каждую папку в путь, после
чего обычные `import gpu`, `from sync import find_offset`, `from autocut import
Clip` продолжают работать без единого изменения в вызывающем коде.

Импортируется из модулей, которым нужен соседний модуль. Сам по себе
ничего не делает, кроме регистрации путей.
"""

from __future__ import annotations

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


install()
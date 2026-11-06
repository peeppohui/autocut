"""Движок AutoCut v2.

Панель Premiere — единственный интерфейс. Всё, что делает монтаж,
живёт здесь и не зависит от Tk, MCP или старых режимов.

Пакет `engine` лежит рядом с `src`, а проверенное ядро — внутри `src`.
Поэтому пути добавляются здесь один раз: иначе `engine.actions` не
найдёт `modes.digest`, а `mcp_server` не увидит `engine`.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # plugin/
SRC = ROOT / "src"                                 # plugin/src/

for _path in (str(ROOT), str(SRC), str(SRC / "config")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

try:
    import bootstrap  # noqa: F401  — добавляет core/modes/ml/io/config
except Exception:
    # Ядро может быть недоступно в урезанной установке; движок всё
    # равно должен импортироваться, чтобы панель показала ошибку.
    pass

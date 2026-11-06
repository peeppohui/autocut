# -*- coding: utf-8 -*-
"""Совместимость: старый путь `mcp_server.jobs` ведёт в `engine.jobs`.

MCP пока остаётся транспортом для агента, но очередь задач теперь одна
и живёт в движке. Иначе панель и MCP видели бы разные списки задач.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]      # plugin/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.jobs import (  # noqa: E402,F401
    BASE,
    FINISHED_STATES,
    Job,
    LOG_LIMIT,
    RUNNER,
    RUN_LOCK,
    Runner,
    SRC,
)

__all__ = [
    "BASE", "FINISHED_STATES", "Job", "LOG_LIMIT", "RUNNER", "RUN_LOCK",
    "Runner", "SRC",
]

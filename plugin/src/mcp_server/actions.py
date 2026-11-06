# -*- coding: utf-8 -*-
"""Совместимость: старый путь `mcp_server.actions` ведёт в движок.

MCP-сервер пока пользуется этими функциями, но канонический код и
очередь задач живут в `engine`. Важно: `jobs` здесь — модуль движка,
поэтому `actions.jobs.RUNNER` и `engine.jobs.RUNNER` — один объект.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]      # plugin/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import jobs  # noqa: E402,F401
from engine.actions import (  # noqa: E402,F401
    cancel,
    digest,
    doctor,
    get,
    interview,
    list_tasks,
    material,
)

__all__ = [
    "jobs", "cancel", "digest", "doctor", "get", "interview", "list_tasks",
    "material",
]

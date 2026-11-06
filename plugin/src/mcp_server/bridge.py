# -*- coding: utf-8 -*-
"""Совместимость: старый путь `mcp_server.bridge` ведёт в `engine.api`.

Панель уже говорит с движком напрямую, но MCP-сервер и старые тесты
пока импортируют этот модуль. Очередь одна: `BRIDGE` здесь — тот же
объект, что и в `engine.api`.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]      # plugin/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.api import (  # noqa: E402,F401
    BRIDGE,
    DEFAULT_PORT,
    MAX_BODY,
    PORTS,
    Bridge,
    Handler,
    Server,
    health,
    origin_allowed,
    serve,
    start,
)

__all__ = [
    "BRIDGE", "DEFAULT_PORT", "MAX_BODY", "PORTS", "Bridge", "Handler",
    "Server", "health", "origin_allowed", "serve", "start",
]

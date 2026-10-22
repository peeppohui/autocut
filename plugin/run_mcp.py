# -*- coding: utf-8 -*-
"""Запуск MCP-сервера AutoCut.

    python run_mcp.py

Отдельный файл, а не `python -m src.mcp_server.server`, потому что
клиент MCP запускает сервер своей командой и из своей папки: модуль
надо найти независимо от текущего каталога.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from src.mcp_server.server import main   # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

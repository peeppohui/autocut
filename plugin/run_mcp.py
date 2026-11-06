# -*- coding: utf-8 -*-
"""Запуск движка AutoCut.

    python run_mcp.py                 # MCP-сервер для агента
    python run_mcp.py --bridge-only   # только HTTP-движок для панели

Отдельный файл, а не `python -m src.mcp_server.server`, потому что
клиент MCP запускает сервер своей командой и из своей папки: модуль
надо найти независимо от текущего каталога.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

if __name__ == "__main__":
    # Панель запускает движок без stdin. В этом режиме нужен только
    # HTTP-движок: MCP stdio сразу увидел бы EOF и завершил процесс.
    if "--bridge-only" in sys.argv[1:] or "--engine" in sys.argv[1:]:
        from engine.api import serve
        sys.exit(serve())
    from src.mcp_server.server import main
    sys.exit(main())

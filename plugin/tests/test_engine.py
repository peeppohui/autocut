# -*- coding: utf-8 -*-
"""Движок v2: один API, одна очередь, старые пути — обёртки.

Проверяем не «модуль импортировался», а что панель, MCP и тесты видят
один и тот же `BRIDGE` и один `RUNNER`. Иначе задачи, поставленные
панелью, не найдутся в MCP, и наоборот.
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "src"), str(ROOT / "src" / "config")):
    if p not in sys.path:
        sys.path.insert(0, p)

fail = []


def ok(name, cond, detail=""):
    if cond:
        print("OK   " + name)
    else:
        fail.append(name)
        print("FAIL " + name + "  " + detail)


from engine import actions, api, jobs, material  # noqa: E402
from mcp_server import actions as mcp_actions  # noqa: E402
from mcp_server import bridge as mcp_bridge  # noqa: E402
from mcp_server import jobs as mcp_jobs  # noqa: E402

ok("engine.actions использует engine.jobs", actions.jobs is jobs)
ok("старый actions ведёт в engine",
   mcp_actions.digest is actions.digest and
   mcp_actions.interview is actions.interview)
ok("старый actions видит ту же очередь", mcp_actions.jobs is jobs)
ok("старый jobs видит ту же очередь", mcp_jobs.RUNNER is jobs.RUNNER)
ok("старый bridge видит тот же BRIDGE", mcp_bridge.BRIDGE is api.BRIDGE)
ok("health отвечает", api.health().get("ok") is True and
   api.health().get("версия") == "v2")
ok("material доступен движку", callable(material.material))

srv = api.Server(("127.0.0.1", 0), api.Handler)
th = threading.Thread(target=srv.serve_forever, daemon=True)
th.start()
try:
    port = srv.server_address[1]
    with urllib.request.urlopen(
            "http://127.0.0.1:{}/health".format(port), timeout=5) as r:
        data = json.loads(r.read().decode("utf-8"))
    ok("GET /health работает", data.get("ok") is True)
finally:
    srv.shutdown()
    srv.server_close()

if fail:
    print("ENGINE FAIL ({})".format(", ".join(fail)))
    sys.exit(1)

print("ENGINE OK")

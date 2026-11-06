# -*- coding: utf-8 -*-
"""Мост в Premiere: какие Origin пускать, а какие нет.

Это не полноценная авторизация, а защита от CSRF: браузерная страница
с чужого сайта не должна дёргать /action и /command на localhost.
Панель CEP приходит без Origin либо с file:///null, поэтому эти
варианты обязаны проходить.
"""
import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
import bootstrap  # noqa: F401,F403

from mcp_server import bridge

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


allowed = [
    "",
    None,
    "   ",
    "null",
    "file://",
    "file:///C:/AutoCut/panel/index.html",
    "http://127.0.0.1:8931",
    "http://localhost:8931",
    "https://127.0.0.1",
    "http://[::1]:8931",
    "HTTP://LOCALHOST:8931",
]
for origin in allowed:
    ok("Origin разрешён: {!r}".format(origin),
       bridge.origin_allowed(origin), str(origin))

denied = [
    "https://evil.com",
    "http://127.0.0.1.evil.com",
    "http://localhost.evil.com",
    "http://127.0.0.1@evil.com",
    "https://evil.com/127.0.0.1",
    "ftp://127.0.0.1",
    "file://evil.com",
]
for origin in denied:
    ok("Origin запрещён: {!r}".format(origin),
       not bridge.origin_allowed(origin), str(origin))

print()
if fail:
    print(f"BRIDGE FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("BRIDGE OK")

# -*- coding: utf-8 -*-
"""MCP-сервер AutoCut: протокол, инструменты и мост в Premiere.

Проверяется тем же способом, как всё остальное: поднимаем сервер, как
это делает клиент, говорим с ним по протоколу и смотрим ответ.

Отдельно проверяется мост в Premiere: панели под рукой нет, поэтому
её роль играет тест — забирает команду, отвечает и получает ответ.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "config"))
import bootstrap  # noqa: F401,F403

fail = []


def ok(name, cond, extra=""):
    if cond:
        print(f"OK   {name}")
    else:
        print(f"FAIL {name} {extra}")
        fail.append(name)


class Client:
    """Минимальный клиент MCP: initialize, tools/list, tools/call."""

    def __init__(self):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        self.p = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "run_mcp.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", bufsize=1, env=env, cwd=str(ROOT))
        self.n = 0

    def send(self, method, params=None, notify=False):
        self.n += 1
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            msg["id"] = self.n
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def recv(self, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.p.stdout.readline()
            if not line:
                return None
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("id") is not None:
                return d
        return None

    def call(self, name, args=None):
        self.send("tools/call", {"name": name, "arguments": args or {}})
        d = self.recv()
        if not d or "result" not in d:
            return {"ok": False, "error": "нет ответа"}
        text = d["result"].get("content", [{}])[0].get("text", "")
        try:
            return json.loads(text)
        except Exception:
            return {"ok": True, "текст": text}

    def close(self):
        try:
            self.p.stdin.close()
            self.p.terminate()
        except Exception:
            pass


# --- протокол -----------------------------------------------------------

c = Client()
c.send("initialize", {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "test", "version": "1"}})
d = c.recv()
ok("сервер отвечает на initialize", bool(d and d.get("result")),
   str(d)[:160])
c.send("notifications/initialized", notify=True)
c.send("tools/list")
d = c.recv()
tools = (d or {}).get("result", {}).get("tools", [])
names = [t["name"] for t in tools]
ok("сервер показывает инструменты", len(names) >= 10, str(names))
ok("есть разбор материала", "autocut_material" in names, str(names))
ok("есть сборка XML", "autocut_build_xml" in names, str(names))
ok("есть импорт в Premiere", "premiere_import" in names, str(names))
ok("у всех инструментов есть описание",
   all((t.get("description") or "").strip() for t in tools), str(names))

# --- инструмент разбора материала ---------------------------------------

tmp = Path(tempfile.mkdtemp())
(tmp / "исх" / "стендап").mkdir(parents=True)
(tmp / "исх" / "нарезка").mkdir(parents=True)
(tmp / "исх" / "интервью").mkdir(parents=True)
for name in ("стендап", "нарезка", "интервью"):
    (tmp / "исх" / name / ("A001.MP4")).write_bytes(b"x" * 1024)
res = c.call("autocut_material", {"folder": str(tmp)})
ok("материал найден во вложенной папке", res.get("ok") is True, str(res)[:200])
parts = res.get("части", {})
ok("части на месте", len(parts) == 3, str(list(parts)))
ok("у части есть число файлов",
   all("файлов" in v for v in parts.values()), str(parts))

res = c.call("autocut_material", {"folder": str(tmp / "нет-такой")})
ok("нет папки — честная ошибка", res.get("ok") is False, str(res)[:120])

# --- чтение плана и полки ----------------------------------------------

plan = tmp / "plan.csv"
plan.write_text("n,file,part,path,in,out,duration,desc\n"
                "1,A001.MP4,нарезка,исх/нарезка/A001.MP4,0.0,2.0,2.0,тема\n",
                encoding="utf-8")
res = c.call("autocut_plan", {"plan_csv": str(plan)})
ok("план читается", res.get("ok") and res.get("строк") == 1, str(res)[:160])

res = c.call("autocut_plan", {"plan_csv": str(tmp / "нет.csv")})
ok("нет плана — честная ошибка", res.get("ok") is False, str(res)[:120])

# --- настройки ----------------------------------------------------------

res = c.call("autocut_settings", {"prefix": "digest."})
ok("настройки читаются", res.get("ok") is True and
   any(k.startswith("digest.") for k in res.get("настройки", {})),
   str(list(res.get("настройки", {}))[:5]))

c.close()

# --- мост в Premiere: панель играет тест -------------------------------

from mcp_server import bridge       # noqa: E402

port = bridge.start()
ok("мост поднялся", port is not None, str(port))
if port:
    import urllib.request
    body = json.dumps({"id": "x1", "ok": True,
                        "данные": "OK|импортировано объектов: 1"}
                       ).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:{}/result".format(port),
                                 data=body,
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()
    got = bridge.BRIDGE.wait("x1", timeout=5)
    ok("ответ панели доходит до сервера",
       got is not None and got.get("ok") is True, str(got))

    cid = bridge.BRIDGE.submit("import_xml", {"path": "C:/x/ryba.xml"})
    got = bridge.BRIDGE.take()
    ok("команда попадает в панель",
       got.get("id") == cid and got.get("cmd") == "import_xml", str(got))
    bridge.BRIDGE.finish(cid, True, "OK|готово")
    res = bridge.BRIDGE.wait(cid, timeout=5)
    ok("ответ по команде возвращается",
       res is not None and res.get("данные") == "OK|готово", str(res))

print()
if fail:
    print(f"MCP FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("MCP OK")

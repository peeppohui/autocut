# -*- coding: utf-8 -*-
"""Панель Premiere: импорт XML, отмена, доктор и автозапуск движка.

Это статическая проверка, а не запуск CEP: в тестах нет ни Premiere,
ни Node. Она ловит ровно те ошибки, которые уже стоили импорта:
`importFiles` вместо `importXML`, потерянный колбэк импорта и
отсутствие кнопки отмены.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "premiere_panel"
JS = (PANEL / "panel.js").read_text(encoding="utf-8")
HTML = (PANEL / "index.html").read_text(encoding="utf-8")
INSTALL = (ROOT / "src" / "mcp_server" / "premiere_install.py").read_text(
    encoding="utf-8")
BRIDGE = (ROOT / "engine" / "api.py").read_text(encoding="utf-8")
ACTIONS = (ROOT / "engine" / "actions.py").read_text(encoding="utf-8")
RUN_MCP = (ROOT / "run_mcp.py").read_text(encoding="utf-8")
SERVER = (ROOT / "src" / "mcp_server" / "server.py").read_text(
    encoding="utf-8")

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


ok("XML импортируется через importXML",
   "app.project.importXML(f)" in JS)
ok("importFiles больше не используется",
   "app.project.importFiles" not in JS,
   "панель снова импортирует XML как обычный файл")
ok("импорт вызывается с колбэком",
   "importXml(xml, function" in JS,
   "watchTask не дождётся результата импорта")
ok("есть кнопка отмены", 'id="btn-cancel"' in HTML)
ok("есть кнопка проверки движка", 'id="btn-doctor"' in HTML)
ok("отмена подключена к мосту",
   'action: "cancel"' in JS and 'name == "cancel"' in BRIDGE)
ok("доктор подключён к мосту",
   "runDoctor" in JS and 'name == "doctor"' in BRIDGE)
ok("доктор есть в действиях", "def doctor()" in ACTIONS)
ok("движок запускается сам", "startEngine" in JS and "run_mcp.py" in JS)
ok("панель ходит в мост через fetch",
   "var NODE_HTTP = null" in JS and "fetch(" in JS,
   "Node-http в CEP зависает на localhost")
ok("поиск порта не зависает",
   "timer = setTimeout" in JS and "}, 1200);" in JS,
   "занятый порт без ответа блокирует автозапуск движка")
ok("Node ищется и в cep_node",
   "function nodeRequire" in JS and "cep_node" in JS and
   "window.require" in JS,
   "в Premiere 26 window.require пуст, движок не запустится")
ok("движок живёт без MCP stdio",
   '"--bridge-only"' in JS and '"--bridge-only"' in RUN_MCP and
   "def serve_bridge()" in SERVER,
   "detached-процесс с закрытым stdin сразу завершится")
ok("путь движка пишет установщик",
   "engine_path.txt" in JS and "engine_path.txt" in INSTALL)
ok("панель знает портативный Python",
   "engine_python.txt" in JS and "engine_python.txt" in INSTALL and
   "sys.executable" in INSTALL)
ok("панель знает PATH для ffmpeg",
   "engine_env.txt" in JS and "engine_env.txt" in INSTALL and
   "shutil.which" in INSTALL)
ok("установщик ищет манифест в CSXS",
   '"CSXS"' in INSTALL and '"manifest.xml"' in INSTALL)

if fail:
    print("PANEL FAIL ({})".format(", ".join(fail)))
    sys.exit(1)
print("PANEL OK")

# -*- coding: utf-8 -*-
"""Панель Premiere: импорт XML, отмена, доктор и автозапуск движка.

Это статическая проверка, а не запуск CEP: в тестах нет ни Premiere,
ни Node. Она ловит ровно те ошибки, которые уже стоили импорта:
`importFiles` вместо `importXML`, потерянный колбэк импорта и
отсутствие кнопки отмены.
"""
import re
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
ok("в Premiere 26 есть запасной путь через importFiles",
   "app.project.importFiles(f.fsName,false,null,false)" in JS,
   "Premiere 26.3.2 доказал отсутствие importXML живым проектом")
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
ok("панель забирает команды из очереди",
   'getJson("/next"' in JS and "function pollCommands" in JS,
   "команды агента висят без ответа, premiere_import не дождётся")
ok("результат команды уходит в мост",
   'postJson("/result"' in JS and "cmd.id" in JS,
   "мост не узнает, чем кончился импорт")
ok("есть отладочная дверь пробной команды",
   "probe: function" in JS,
   "поиск нужного API Premiere без неё требует переустановки панели")

# --- оформление по макету бренда -----------------------------------------
#
# Панель — это HTML+CSS в Premiere, поэтому макет переносится почти
# буквально. Проверяем, что всё на месте: разметка, шрифты, логотип.
ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', HTML))
need = {"source-name", "source-meta", "dur-value", "target", "folder",
        "btn-find", "btn-digest", "btn-cancel", "btn-check", "btn-doctor",
        "btn-refresh", "dot", "state", "parts", "log", "draft-title",
        "draft-time", "draft-timeline", "draft-note", "chips"}
ok("разметка макета на месте", need <= ids, sorted(need - ids))
ok("шрифты подключены локально",
   HTML.count("@font-face") == 4 and "assets/fonts/" in HTML,
   "в Premiere панели нет интернета — шрифты должны лежать рядом")
ok("кириллица описана отдельным диапазоном", "U+0400-045F" in HTML)
for _f in ("Manrope-400-latin.woff2", "Manrope-400-cyrillic.woff2",
           "JetBrainsMono-400-latin.woff2", "JetBrainsMono-400-cyrillic.woff2"):
    ok("шрифт {} лежит рядом с панелью".format(_f),
       (PANEL / "assets" / "fonts" / _f).exists())
ok("логотип лежит рядом с панелью",
   (PANEL / "assets" / "logo.png").exists())
ok("палитра макета в стилях",
   "#0D0E1C" in HTML and "#7C4DFF" in HTML and "#4D8DFF" in HTML)
ok("тумблеры видны и собираются",
   "function flags()" in JS and 'data-flag="cut_pauses"' in HTML)
ok("черновик рисуется в футере",
   "function drawDraft" in JS and 'id="draft-timeline"' in HTML)
ok("сводка черновика спрашивается у движка",
   'action: "summary"' in JS and 'name == "summary"' in BRIDGE)
ok("движок умеет сводку", "def summary(" in ACTIONS)
ok("выбор папки диалогом Premiere", "Folder.selectDialog" in JS)

if fail:
    print("PANEL FAIL ({})".format(", ".join(fail)))
    sys.exit(1)
print("PANEL OK")

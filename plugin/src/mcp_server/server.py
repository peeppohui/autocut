# -*- coding: utf-8 -*-
"""AutoCut как MCP-сервер.

Что это даёт: клиент-агент (opencode, Claude Code, Claude Desktop) видит
инструменты AutoCut и может сам разобрать материал, собрать рыбу,
положить результат в Premiere — без того, чтобы человек кликал кнопки
окна.

Запуск:

    python -m src.mcp_server.server

Клиент настраивается на эту команду. Сервер говорит по stdio, поэтому
логи сервера нельзя писать в stdout: stdout — это протокол. Всё, что
должно быть видно, уходит в stderr.
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SRC / "config") not in sys.path:
    sys.path.insert(0, str(SRC / "config"))
# Папка src — чтобы `mcp_server` был обычным пакетом и внутри пакета,
# и снаружи (как `python -m src.mcp_server.server`).
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import bootstrap            # noqa: E402,F401

from mcp_server import bridge, jobs, tools       # noqa: E402

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:      # mcp 1.x — там был FastMCP
    from mcp.server.fastmcp import FastMCP as MCPServer


INSTRUCTIONS = """\
AutoCut — монтаж рыбы для Premiere Pro: синхронизация камер и микрофонов,
нарезка интервью, сборка дайджеста из трёх частей материала (стендап,
нарезка, интервью), экспорт в FCP7 XML и EDL, который Premiere импортирует.

Как пользоваться:
* autocut_material — сначала узнай, где материал. Сервер сам ищет папку с
  частями внутри (исх, исходники, видео) и говорит, если нашёл её не
  сразу.
* autocut_digest и autocut_interview — долгие (минуты). Они возвращают
  номер задачи: дальше зови autocut_job и смотри журнал. Не зови их
  снова, пока задача идёт, и не делай в это время ничего тяжёлого.
* autocut_build_xml — собрать XML по готовому плану. Это быстро.
* Готовый результат: plan.csv (что попало в рыбу), shelf.csv (полка
  монтажёра: что в кадре, что в звуке, куда попало).
"""


def _digest_argv(folder: str, out: str, target_sec=None, model: str = "large-v3"):
    argv = [str(SRC / "modes" / "digest.py"), "--input", str(folder),
            "--out", str(out), "--model", model]
    if target_sec:
        argv += ["--target-sec", str(target_sec)]
    return argv


def _interview_argv(folder: str, out: str, model: str = "large-v3",
                    target=None, wide: str = "", guest_cam: str = "",
                    guest_mic: str = "", host_mic: str = ""):
    argv = [str(SRC / "modes" / "interview.py"), "--input", str(folder),
            "--outdir", str(out), "--model", model]
    if target:
        argv += ["--target", str(target)]
    if wide:
        argv += ["--wide", wide]
    if guest_cam:
        argv += ["--guest-cam", guest_cam]
    if guest_mic:
        argv += ["--guest-mic", guest_mic]
    if host_mic:
        argv += ["--host-mic", host_mic]
    return argv


def build_server():
    srv = MCPServer(name="autocut", instructions=INSTRUCTIONS,
                    version="1.0.0")

    @srv.tool(
        description="Что лежит в папке: части материала (стендап, нарезка, "
                    "интервью), сколько в них файлов и секунд. Сервер сам "
                    "ищет папку с частями внутри и говорит, если нашёл "
                    "не сразу. Вызывай первым.")
    def autocut_material(folder: str) -> dict:
        return tools.material(folder)

    @srv.tool(
        description="Собрать дайджест: стендап, нарезка, интервью -> "
                    "plan.csv и shelf.csv. Долго (минуты): возвращает "
                    "номер задачи, дальше autocut_job.")
    def autocut_digest(folder: str, out: str, target_sec: str = "",
                       model: str = "large-v3") -> dict:
        job = jobs.RUNNER.start("дайджест", _digest_argv(
            folder, out, target_sec or None, model))
        return {"ok": True, "задача": job,
                "смотри": "autocut_job('{}')".format(job)}

    @srv.tool(
        description="Собрать рыбу по интервью: синхронизация, выбор "
                    "камеры по словам, нарезка по паузам. Долго: "
                    "возвращает номер задачи, дальше autocut_job.")
    def autocut_interview(folder: str, out: str, model: str = "large-v3",
                          target: str = "", wide: str = "",
                          guest_cam: str = "", guest_mic: str = "",
                          host_mic: str = "") -> dict:
        job = jobs.RUNNER.start("интервью", _interview_argv(
            folder, out, model, target or None, wide, guest_cam, guest_mic,
            host_mic))
        return {"ok": True, "задача": job,
                "смотри": "autocut_job('{}')".format(job)}

    @srv.tool(description="Состояние фоновой задачи: идёт ли, журнал, "
                          "ошибка. Задача — это то, что вернули "
                          "autocut_digest или autocut_interview.")
    def autocut_job(job: str) -> dict:
        info = jobs.RUNNER.get(job)
        if not info:
            return {"ok": False, "error": "нет задачи " + job}
        return {"ok": True, **info.info()}

    @srv.tool(description="Все фоновые задачи и их состояние.")
    def autocut_jobs() -> dict:
        return {"ok": True, "задачи": jobs.RUNNER.list()}

    @srv.tool(description="Отменить фоновую задачу.")
    def autocut_cancel(job: str) -> dict:
        return {"ok": jobs.RUNNER.cancel(job), "задача": job}

    @srv.tool(description="Прочитать plan.csv: что попало в рыбу, по "
                          "кускам — файл, откуда, сколько секунд, о чём "
                          "речь.")
    def autocut_plan(plan_csv: str) -> dict:
        return tools.read_csv(plan_csv)

    @srv.tool(description="Прочитать shelf.csv: полка монтажёра по всей "
                          "нарезке — что в кадре, что в звуке клипа, "
                          "куда попал в рыбу.")
    def autocut_shelf(shelf_csv: str, limit: int = 0) -> dict:
        return tools.read_csv(shelf_csv, limit)

    @srv.tool(description="Собрать FCP7 XML (и EDL) из плана. Premiere "
                          "импортирует такой XML: File > Import. "
                          "media_dir — корень, относительно которого "
                          "считаются пути в плане.")
    def autocut_build_xml(plan_csv: str, out: str, media_dir: str = "",
                          name: str = "", edl: str = "",
                          fps: int = 25) -> dict:
        return tools.build_xml(plan_csv, out, media_dir, name, edl, fps)

    @srv.tool(description="Настройки AutoCut. Без префикса — все; "
                          "с префиксом (например 'digest.') — одна "
                          "секция.")
    def autocut_settings(prefix: str = "") -> dict:
        return tools.settings_get(prefix)

    @srv.tool(description="Записать настройку AutoCut.")
    def autocut_set_setting(key: str, value: str) -> dict:
        return tools.settings_set(key, value)

    # --- мост в Premiere Pro ---

    @srv.tool(description="Поставить панель AutoCut в Premiere Pro: "
                          "копирует панель в папку расширений и включает "
                          "режим отладки в реестре. После этого Premiere "
                          "надо перезапустить и открыть Окно -> "
                          "Расширения -> AutoCut.")
    def premiere_install_panel() -> dict:
        from mcp_server import premiere_install
        return premiere_install.install_panel()

    @srv.tool(description="Связь с Premiere: панель на связи, сколько "
                          "команд ждёт, стоит ли панель. Панель должна быть "
                          "открыта в Premiere (Окно -> Расширения -> AutoCut).")
    def premiere_status() -> dict:
        from mcp_server import premiere_install
        return {"ok": True, "панель_установлена": premiere_install.is_installed(),
                "мост": bridge.BRIDGE.status()}

    @srv.tool(description="Импортировать готовую рыбу в Premiere: "
                          "указываешь путь к XML (например ryba.xml) — он "
                          "ложится в открытый проект и создаёт секвенцию. "
                          "Premiere должен быть открыт, панель — на связи.")
    def premiere_import(xml_path: str, wait: int = 60) -> dict:
        p = Path(xml_path).expanduser()
        if not p.is_file():
            return {"ok": False, "error": "нет файла: " + str(p)}
        cid = bridge.BRIDGE.submit("import_xml", {"path": str(p)})
        res = bridge.BRIDGE.wait(cid, timeout=float(wait))
        if res is None:
            return {"ok": False,
                    "error": "панель не ответила за {} с. Открыта ли панель "
                             "AutoCut в Premiere?".format(wait)}
        return {"ok": bool(res.get("ok")), "xml": str(p),
                "ответ": res.get("данные")}

    @srv.tool(description="Что в открытом проекте Premiere: имя проекта, "
                          "секвенции, активная секвенция.")
    def premiere_project_info(wait: int = 30) -> dict:
        cid = bridge.BRIDGE.submit("project_info", {})
        res = bridge.BRIDGE.wait(cid, timeout=float(wait))
        if res is None:
            return {"ok": False, "error": "панель не ответила"}
        return {"ok": bool(res.get("ok")), "ответ": res.get("данные")}

    @srv.tool(description="Клипы активной секвенции Premiere: дорожки, "
                          "имена, таймкоды. Так видно, что попало в "
                          "секвенцию после импорта.")
    def premiere_clips(wait: int = 30) -> dict:
        cid = bridge.BRIDGE.submit("sequence_clips", {})
        res = bridge.BRIDGE.wait(cid, timeout=float(wait))
        if res is None:
            return {"ok": False, "error": "панель не ответила"}
        return {"ok": bool(res.get("ok")), "ответ": res.get("данные")}

    @srv.tool(description="Поставить маркер на активной секвенции Premiere "
                          "(секунда и название).")
    def premiere_add_marker(time: float, name: str = "AutoCut",
                            wait: int = 30) -> dict:
        cid = bridge.BRIDGE.submit("add_marker",
                                   {"time": float(time), "name": name})
        res = bridge.BRIDGE.wait(cid, timeout=float(wait))
        if res is None:
            return {"ok": False, "error": "панель не ответила"}
        return {"ok": bool(res.get("ok")), "ответ": res.get("данные")}

    return srv


def main(argv=None) -> int:
    port = bridge.start()
    if port:
        print("мост в Premiere: http://127.0.0.1:{}".format(port),
              file=sys.stderr)
    else:
        print("мост в Premiere не поднялся: порты заняты", file=sys.stderr)
    srv = build_server()
    srv.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())

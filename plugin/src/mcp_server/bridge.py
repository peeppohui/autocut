# -*- coding: utf-8 -*-
"""Мост в Premiere Pro.

**Что это.** Панель CEP внутри Premiere опрашивает этот сервер и
выполняет команды, которые приходят из агента: импорт XML, чтение
состава секвенции. Обратно панель отдаёт результат.

**Почему панель, а не «Файл → Скрипты».** В Premiere 26 пункт
«Файл → Скрипты» убран, и на этом заканчиваются все пути в Premiere
извне. Но панель CEP умеет `evalScript` — это тот же ExtendScript,
просто вызывается из панели, а не из меню. Панели CEP в Premiere 26
работают, и Creative Cloud для них не нужен (это нужно для UXP).

**Кто что делает.**
- MCP-инструмент `premiere_import` кладёт команду в очередь.
- Панель забирает команду (`GET /next`) и выполняет её в Premiere.
- Панель отдаёт результат (`POST /result`), MCP-инструмент его ждёт.

Сервер поднимает HTTP только на 127.0.0.1: панель — локальная, и
наружу ничего не смотрит.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8931
# Панель перебирает порты по очереди, и сервер должен выбирать так же,
# иначе они разойдутся: сервер встанет на 8932, а панель пойдёт по
# 8931 и упрётся в чужой сервер.
PORTS = (8931, 8932, 8933)


class Bridge:
    """Очередь команд и ответов между агентом и панелью."""

    def __init__(self):
        self.pending = []          # команды, которые панель заберёт
        self.results = {}          # id -> результат
        self.pings = 0             # сколько раз спросили /ping
        self.polls = 0             # сколько раз панель забрала команду
        self.last_seen = 0.0
        self._lock = threading.Lock()
        self._events = threading.Condition(self._lock)

    def submit(self, cmd: str, args: dict = None) -> str:
        cid = uuid.uuid4().hex[:8]
        with self._lock:
            self.pending.append({"id": cid, "cmd": cmd, "args": args or {}})
            self._events.notify_all()
        return cid

    def take(self) -> dict:
        """Забрать следующую команду (долгий опрос — панель ждёт)."""
        with self._lock:
            if not self.pending:
                self._events.wait(timeout=20.0)
            if not self.pending:
                return {}
            return self.pending.pop(0)

    def finish(self, cid: str, ok: bool, data) -> None:
        with self._lock:
            self.results[cid] = {"ok": bool(ok), "данные": data}
            self._events.notify_all()

    def wait(self, cid: str, timeout: float = 30.0):
        deadline = time.time() + timeout
        with self._lock:
            while cid not in self.results:
                left = deadline - time.time()
                if left <= 0:
                    return None
                self._events.wait(timeout=min(left, 1.0))
            return self.results.pop(cid)

    def status(self) -> dict:
        # Панель забирает команды через /next, а проверка связи идёт
        # через /ping. Считать их одним счётчиком нельзя: агент сам
        # проверяет связь и ping'ов больше, чем опросов панели.
        with self._lock:
            last = (time.time() - self.last_seen) if self.last_seen else None
            return {"панель_на связи": self.polls > 0,
                    "опросов_панели": self.polls,
                    "проверок_связи": self.pings,
                    "секунд_с_последнего": round(last, 1) if last else None,
                    "в_очереди": len(self.pending)}


BRIDGE = Bridge()


class Handler(BaseHTTPRequestHandler):
    """Только три маршрута: /next, /result, /ping."""

    def log_message(self, fmt, *args):
        pass                      # в stdout писать нельзя: это протокол

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/next"):
            with BRIDGE._lock:
                BRIDGE.polls += 1
                BRIDGE.last_seen = time.time()
            cmd = BRIDGE.take()
            self._send(200, cmd or {})
            return
        if self.path.startswith("/ping"):
            # Блокировку отпускаем ДО вызова status(): status её тоже
            # берёт, а обычный Lock не переиспользуемый — иначе тут
            # висел бы каждый запрос.
            with BRIDGE._lock:
                BRIDGE.pings += 1
                BRIDGE.last_seen = time.time()
            self._send(200, BRIDGE.status())
            return
        self._send(404, {"error": "нет такого маршрута"})

    def do_POST(self):
        if self.path.startswith("/result"):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8", "replace")
            try:
                data = json.loads(raw)
            except Exception:
                data = {"ok": False, "данные": "не разобрал ответ"}
            BRIDGE.finish(data.get("id", ""), data.get("ok", False),
                          data.get("данные"))
            self._send(200, {"ok": True})
            return
        self._send(404, {"error": "нет такого маршрута"})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()


class Server(ThreadingHTTPServer):
    """Сервер моста.

    `allow_reuse_address` выключен НАМЕРЕННО: на Windows с ним можно
    сесть на порт, который уже занят, и тогда на одном порте окажутся
    два сервера — и кто из них ответит на запрос, угадать нельзя.
    """

    allow_reuse_address = False
    daemon_threads = True


def start(port: int = 0):
    """Поднять мост в фоне. Возвращает порт или None, если не вышло.

    `port` = 0 — перебрать PORTS по очереди и взять первый свободный.
    """
    ports = (port,) if port else PORTS
    for p in ports:
        try:
            srv = Server(("127.0.0.1", p), Handler)
        except OSError:
            continue
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        return p
    return None

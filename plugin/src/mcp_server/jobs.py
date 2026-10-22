# -*- coding: utf-8 -*-
"""Фоновые задачи: тяжёлое считается отдельно, агент не ждёт.

Сборка дайджеста — это минуты: распознавание речи, описание кадров,
подбор. Вызов инструмента не может столько висеть (у клиентов MCP
есть свои таймауты), поэтому такая работа уходит в отдельный процесс,
а инструмент сразу отдаёт номер задачи.

Процесс отдельный ещё и потому, что он занимает видеопамять: внутри
сервера модель Whisper и кодировщик темы в 6 ГБ не влезут, а так
память освобождается вместе с процессом.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
BASE = SRC.parent
LOG_LIMIT = 400          # строк журнала, которые держим в памяти


class Job:
    """Одна фоновая задача."""

    def __init__(self, job_id: str, title: str, argv):
        self.id = job_id
        self.title = title
        self.argv = list(argv)
        self.proc = None
        self.state = "в очереди"       # в очереди / идёт / готово / ошибка / отменено
        self.lines = []
        self.error = ""
        self.started = time.time()
        self.finished = 0.0
        self.result = None
        self._lock = threading.Lock()

    def add(self, line: str) -> None:
        with self._lock:
            self.lines.append(line.rstrip())
            if len(self.lines) > LOG_LIMIT:
                del self.lines[:len(self.lines) - LOG_LIMIT]

    def info(self) -> dict:
        with self._lock:
            return {
                "id": self.id, "задача": self.title, "состояние": self.state,
                "секунд": round((self.finished or time.time()) - self.started, 1),
                "ошибка": self.error,
                "результат": self.result,
                "журнал": list(self.lines[-40:])}


class Runner:
    """Фоновые задачи AutoCut."""

    def __init__(self):
        self.jobs = {}
        self._seq = 0
        self._lock = threading.Lock()

    def start(self, title: str, argv) -> str:
        with self._lock:
            self._seq += 1
            job_id = "j{}".format(self._seq)
        job = Job(job_id, title, argv)
        self.jobs[job_id] = job
        th = threading.Thread(target=self._run, args=(job,), daemon=True)
        th.start()
        return job_id

    def _run(self, job: Job) -> None:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        job.state = "идёт"
        try:
            p = subprocess.Popen(
                [sys.executable, "-u"] + job.argv, cwd=str(BASE),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                env=env)
            job.proc = p
            for line in p.stdout:
                job.add(line)
            p.wait()
            job.finished = time.time()
            if p.returncode == 0:
                job.state = "готово"
            else:
                job.state = "ошибка"
                job.error = "код возврата {}".format(p.returncode)
        except Exception as e:
            job.finished = time.time()
            job.state = "ошибка"
            job.error = str(e)
            job.add("ОШИБКА запуска: " + str(e))

    def get(self, job_id: str):
        return self.jobs.get(job_id)

    def list(self) -> list:
        return [j.info() for j in self.jobs.values()]

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or not job.proc:
            return False
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(job.proc.pid)],
                           capture_output=True, timeout=15)
        except Exception:
            pass
        job.state = "отменено"
        return True


RUNNER = Runner()

# -*- coding: utf-8 -*-
"""Фоновые задачи: тяжёлое считается отдельно, агент не ждёт.

Сборка дайджеста — это минуты: распознавание речи, описание кадров,
подбор. Вызов инструмента не может столько висеть (у клиентов MCP
есть свои таймауты), поэтому такая работа уходит в отдельный процесс,
а инструмент сразу отдаёт номер задачи.

Процесс отдельный ещё и потому, что он занимает видеопамять: внутри
сервера модель Whisper и кодировщик темы в 6 ГБ не влезут, а так
память освобождается вместе с процессом.

**Один тяжёлый процесс за раз.** Даже если запустить два дайджеста
подряд, второй ждёт в очереди: иначе Whisper, Qwen2-VL и e5-large
одновременно просят видеопамять и роняют друг друга. Очередь живёт
в `RUN_LOCK`, а состояние «в очереди» видно панели.
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
FINISHED_STATES = ("готово", "ошибка", "отменено")

# Общий на процесс замок: один тяжёлый шаг за раз. Он же работает
# очередью: задача, которая не смогла его взять, остаётся «в очереди».
RUN_LOCK = threading.Lock()


def _kill_tree(proc) -> None:
    """Убить процесс и его детей. На Windows — taskkill, иначе terminate."""
    if proc is None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=15)
        except Exception:
            pass
        return
    try:
        proc.terminate()
    except Exception:
        pass


class Job:
    """Одна фоновая задача: один или несколько шагов подряд."""

    def __init__(self, job_id: str, title: str, steps):
        self.id = job_id
        self.title = title
        self.steps = list(steps)       # [(название, argv, результат)]
        self.argv = list(self.steps[0][1])
        self.proc = None
        self.state = "в очереди"       # в очереди / идёт / готово / ошибка / отменено
        self.lines = []
        self.error = ""
        self.started = time.time()
        self.finished = 0.0
        self.result = None
        self.results = []              # [{"шаг": имя, "результат": путь}]
        self.cancel_requested = False
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
                "результаты": list(self.results),
                "журнал": list(self.lines[-40:])}

    def set_state(self, state: str) -> None:
        with self._lock:
            self.state = state

    def set_proc(self, proc) -> None:
        with self._lock:
            self.proc = proc

    def is_cancelled(self) -> bool:
        with self._lock:
            return self.cancel_requested

    def finish(self, state: str, error: str = "") -> None:
        with self._lock:
            self.state = state
            # Отмена уже поставила время. Поток, который дошёл до
            # проверки флага, не должен сдвигать его ещё раз: иначе
            # «секунд» у отменённой задачи продолжает расти.
            if not self.finished:
                self.finished = time.time()
            if error:
                self.error = error

    def add_result(self, name: str, result) -> None:
        with self._lock:
            self.results.append({"шаг": name, "результат": str(result)})

    def set_result(self, result) -> None:
        with self._lock:
            self.result = result

    def cancel(self) -> bool:
        """Отменить задачу в любом состоянии, кроме уже завершённого.

        Если процесс уже запущен — убиваем дерево. Если задача ещё
        в очереди — поток увидит флаг и не начнёт шаг. Время в
        `info()` останавливается сразу, иначе отменённая задача
        «идёт» в панели минутами.
        """
        with self._lock:
            if self.state in FINISHED_STATES:
                return False
            self.cancel_requested = True
            self.state = "отменено"
            self.finished = time.time()
            proc = self.proc
        _kill_tree(proc)
        return True


class Runner:
    """Фоновые задачи AutoCut."""

    def __init__(self):
        self.jobs = {}
        self._seq = 0
        self._lock = threading.Lock()

    def start(self, title: str, argv=None, steps=None) -> str:
        if steps is None:
            if argv is None:
                raise ValueError("нужен argv или steps")
            steps = [(title, argv, None)]
        steps = [(str(n), list(a), r) for n, a, r in steps]
        if not steps:
            raise ValueError("у задачи нет шагов")
        with self._lock:
            self._seq += 1
            job_id = "j{}".format(self._seq)
            job = Job(job_id, title, steps)
            self.jobs[job_id] = job
        th = threading.Thread(target=self._run, args=(job,), daemon=True)
        th.start()
        return job_id

    def _run(self, job: Job) -> None:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        acquired = False
        try:
            # Ждём очередь, но не бесконечно: отменённая задача не
            # должна занять GPU, когда до неё дойдёт ход.
            while not job.is_cancelled():
                if RUN_LOCK.acquire(timeout=0.2):
                    acquired = True
                    break
            if not acquired or job.is_cancelled():
                job.finish("отменено")
                return

            job.set_state("идёт")
            if job.is_cancelled():
                job.finish("отменено")
                return

            last_result = None
            for name, argv, result in job.steps:
                if job.is_cancelled():
                    job.finish("отменено")
                    return
                job.add("=== {} ===".format(name))
                p = subprocess.Popen(
                    [sys.executable, "-u"] + argv, cwd=str(BASE),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    env=env)
                job.set_proc(p)
                # Отмена могла прийти между проверкой выше и запуском:
                # тогда процесса ещё не было и убивать было нечего.
                if job.is_cancelled():
                    _kill_tree(p)
                try:
                    for line in p.stdout:
                        job.add(line)
                    p.wait()
                finally:
                    job.set_proc(None)
                if job.is_cancelled():
                    job.finish("отменено")
                    return
                if p.returncode != 0:
                    if len(job.steps) > 1:
                        error = "шаг «{}»: код {}".format(name, p.returncode)
                    else:
                        error = "код возврата {}".format(p.returncode)
                    job.finish("ошибка", error)
                    return
                if result:
                    job.add_result(name, result)
                    last_result = result

            if job.is_cancelled():
                job.finish("отменено")
                return
            job.finish("готово")
            if last_result:
                job.set_result({"xml": str(last_result)})
        except Exception as e:
            job.finish("ошибка", str(e))
            job.add("ОШИБКА запуска: " + str(e))
        finally:
            if acquired:
                RUN_LOCK.release()

    def get(self, job_id: str):
        return self.jobs.get(job_id)

    def list(self) -> list:
        return [j.info() for j in self.jobs.values()]

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None:
            return False
        return job.cancel()


RUNNER = Runner()

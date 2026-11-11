# -*- coding: utf-8 -*-
"""Фоновые задачи: один шаг, несколько шагов и общий движок с панелью."""
import sys
import tempfile
import time
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
import bootstrap  # noqa: F401,F403

from mcp_server import actions, jobs

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


def wait(runner, job_id, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = runner.get(job_id)
        if job and job.state not in ("в очереди", "идёт"):
            return job.info()
        time.sleep(0.05)
    return (runner.get(job_id).info() if runner.get(job_id) else {})


runner = jobs.Runner()

job = runner.start("один", ["-c", "print('one')"])
info = wait(runner, job)
ok("один шаг доходит до готово", info.get("состояние") == "готово",
   str(info)[:160])
ok("у одного шага нет пути результата", info.get("результат") is None,
   str(info.get("результат")))

with tempfile.TemporaryDirectory() as td:
    xml = _P(td) / "ryba.xml"
    job = runner.start("два", steps=[
        ("первый", ["-c", "print('one')"], str(_P(td) / "plan.csv")),
        ("второй", ["-c", "print('two')"], str(xml)),
    ])
    info = wait(runner, job)
    ok("два шага доходят до готово", info.get("состояние") == "готово",
       str(info)[:160])
    ok("результат — файл последнего шага",
       (info.get("результат") or {}).get("xml") == str(xml),
       str(info.get("результат")))
    ok("в журнале видны оба шага",
       any("первый" in line for line in info.get("журнал", []))
       and any("второй" in line for line in info.get("журнал", [])),
       str(info.get("журнал")))
    ok("сохранены результаты всех шагов",
       len(info.get("результаты", [])) == 2,
       str(info.get("результаты")))

    job = runner.start("сбой", steps=[
        ("первый", ["-c", "print('one')"], ""),
        ("второй", ["-c", "raise SystemExit(3)"], ""),
    ])
    info = wait(runner, job)
    ok("сбой шага останавливает задачу", info.get("состояние") == "ошибка",
       str(info)[:160])
    ok("в ошибке назван шаг",
       "второй" in info.get("ошибка", "") and "3" in info.get("ошибка", ""),
       str(info.get("ошибка")))

# Отмена: и работающая задача, и задача, которая ждёт очередь.
long_id = runner.start("долгая", ["-c", "import time; time.sleep(5)"])
deadline = time.time() + 5.0
while time.time() < deadline:
    long_job = runner.get(long_id)
    if long_job and long_job.state == "идёт":
        break
    time.sleep(0.05)
ok("долгая задача пошла",
   runner.get(long_id).state == "идёт",
   str(runner.get(long_id).info()))

queued_id = runner.start("в очереди", ["-c", "print('never')"])
ok("вторая задача ждёт очередь",
   runner.get(queued_id).state == "в очереди",
   str(runner.get(queued_id).info()))
ok("задача в очереди отменяется", runner.cancel(queued_id),
   str(runner.get(queued_id).info()))
qinfo = wait(runner, queued_id)
ok("отмена в очереди сработала",
   qinfo.get("состояние") == "отменено", str(qinfo))
qsec = qinfo.get("секунд")
time.sleep(0.3)
ok("время отменённой задачи стоит",
   runner.get(queued_id).info().get("секунд") == qsec,
   "{} -> {}".format(qsec, runner.get(queued_id).info().get("секунд")))

ok("работающая задача отменяется", runner.cancel(long_id),
   str(runner.get(long_id).info()))
linfo = wait(runner, long_id)
ok("отмена работающей сработала",
   linfo.get("состояние") == "отменено", str(linfo))
ok("повторная отмена не проходит", not runner.cancel(long_id),
   str(runner.get(long_id).info()))

# Дать потоку дочитать и отпустить RUN_LOCK до следующих проверок.
deadline = time.time() + 3.0
while time.time() < deadline:
    lj = runner.get(long_id)
    if lj is None or lj.proc is None:
        break
    time.sleep(0.05)

# Действия панели должны ставить задачи в тот же движок, а не в свой.
calls = []
real_start = actions.jobs.RUNNER.start


def fake_start(title, argv=None, steps=None):
    calls.append((title, argv, steps))
    return "jX"


actions.jobs.RUNNER.start = fake_start
try:
    with tempfile.TemporaryDirectory() as td:
        res = actions.digest(str(_P(td) / "материал"),
                             str(_P(td) / "результат"), target_sec=90)
finally:
    actions.jobs.RUNNER.start = real_start

ok("дайджест ставится в общий Runner",
   res.get("задача") == "jX" and calls and calls[0][0] == "дайджест",
   str(res))
steps = calls[0][2] if calls else []
ok("дайджест — три шага", len(steps) == 3, str(steps))
first = steps[0][1] if steps else []
second = steps[1][1] if len(steps) > 1 else []
last = steps[-1][1] if steps else []
ok("хронометраж доходит до плана",
   "--target-sec" in first and "90" in first, str(first))
ok("второй шаг собирает секвенцию отбора",
   any(name.endswith("premiere.py") for name in second)
   and "Отбор" in second and "--allow-empty" in second, str(second))
ok("последний шаг собирает XML рыбы",
   any(name.endswith("premiere.py") for name in last)
   and "--media-dir" in last
   # Имя секвенции — «Сбор (AutoCut)»: в проекте уже есть ручная
   # «Сбор», одно имя у двух секвенций путает.
   and any("Сбор" in name for name in last), str(last))
# Панель импортирует XML последнего шага — им должен остаться
# ryba.xml, то есть секвенция «Сбор».
ok("последним шагом идёт сбор",
   str(steps[-1][2]).endswith("ryba.xml"), str(steps[-1][2]))

print()
if fail:
    print(f"JOBS FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("JOBS OK")

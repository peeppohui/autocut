# -*- coding: utf-8 -*-
"""Действия AutoCut для панели в Premiere.

Панель в Premiere — это интерфейс. Тяжёлое (распознавание, описание
кадров, подбор) делает движок, а панель только спрашивает его и
показывает результат.

Задачи долгие, поэтому тяжёлое идёт отдельным процессом (он занимает
видеопамять), а панель спрашивает состояние. Движок задач один —
`engine.jobs`: иначе у панели и у MCP были бы разные списки задач.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

# actions.py лежит в plugin/engine, а проверенное ядро — в plugin/src.
SRC = Path(__file__).resolve().parent.parent / "src"
BASE = SRC.parent

from engine import jobs   # noqa: E402


def material(folder: str) -> dict:
    """Что в папке: части материала, сколько файлов и секунд."""
    from engine.material import material as _material
    return _material(folder)


def digest(folder: str, out: str, target_sec=None, model="large-v3") -> dict:
    """Собрать дайджест: план, полка и две секвенции для Premiere.

    Три шага: сначала план (долго, это и есть сборка), потом XML из
    плана (быстро). Панель следит за задачей и импортирует XML, когда
    он готов.

    Секвенций две: «Сбор» — собранный материал (рыба), «Отбор» —
    не взятые в сборку исходники, чтобы монтажёр подменил кадры, если
    какие-то не понравятся.
    """
    outp = Path(out)
    outp.mkdir(parents=True, exist_ok=True)
    plan = outp / "plan.csv"
    otbor = outp / "otbor.csv"
    xml = outp / "ryba.xml"
    xml_otbor = outp / "ryba_otbor.xml"
    edl = outp / "ryba.edl"
    argv = [str(SRC / "modes" / "digest.py"), "--input", str(folder),
            "--out", str(outp), "--model", model]
    if target_sec:
        argv += ["--target-sec", str(target_sec)]
    steps = [
        ("собираю речь и нарезку", argv, str(plan)),
        # Отбор идёт ПЕРЕД чистовиком: панель импортирует XML последнего
        # шага, и им должен остаться ryba.xml — секвенция «Сбор».
        ("собираю секвенцию «Отбор»",
         [str(SRC / "io" / "premiere.py"), "--timeline", str(otbor),
          "--media-dir", str(folder), "--out", str(xml_otbor), "--edl", "",
          "--name", "Отбор", "--no-markers", "--allow-empty"],
         str(xml_otbor)),
        ("собираю секвенцию «Сбор»",
         [str(SRC / "io" / "premiere.py"), "--timeline", str(plan),
          "--media-dir", str(folder), "--out", str(xml), "--edl", str(edl),
          "--name", "Сбор"], str(xml)),
    ]
    task_id = jobs.RUNNER.start("дайджест", steps=steps)
    return {"ok": True, "задача": task_id, "папка": str(outp)}


def interview(folder: str, out: str, model="large-v3", target=None) -> dict:
    """Собрать рыбу по интервью."""
    outp = Path(out)
    outp.mkdir(parents=True, exist_ok=True)
    argv = [str(SRC / "modes" / "interview.py"), "--input", str(folder),
            "--outdir", str(outp), "--model", model]
    if target:
        argv += ["--target", str(target)]
    task_id = jobs.RUNNER.start(
        "интервью", steps=[("собираю рыбу", argv, str(outp))])
    return {"ok": True, "задача": task_id, "папка": str(outp)}


def get(task_id: str):
    return jobs.RUNNER.get(task_id)


def list_tasks() -> list:
    return jobs.RUNNER.list()


def cancel(task_id: str) -> bool:
    return jobs.RUNNER.cancel(task_id)


def _module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def doctor() -> dict:
    """Проверить, чем движок сможет работать на этой машине.

    Панель показывает результат человеку, а не пытается чинить окружение:
    установка ffmpeg и моделей — отдельное решение, и молчаливая попытка
    что-то скачать закончилась бы непонятной ошибкой через десять минут.
    """
    required = ["numpy", "cv2", "PIL", "faster_whisper", "ultralytics",
                "torch", "transformers", "sentence_transformers"]
    # MCP больше не нужен панели: это временный транспорт, не продукт.
    optional = ["mcp"]
    modules = {name: _module(name) for name in required + optional}

    errors = []
    python = "{}.{}.{}".format(*sys.version_info[:3])
    if sys.version_info < (3, 11):
        errors.append("нужен Python 3.11 или новее, сейчас " + python)
    for name in required:
        if not modules[name]:
            errors.append("нет модуля: " + name)

    ffmpeg = shutil.which("ffmpeg") or ""
    ffprobe = shutil.which("ffprobe") or ""
    if not ffmpeg:
        errors.append("ffmpeg не найден в PATH")
    if not ffprobe:
        errors.append("ffprobe не найден в PATH")

    root = BASE.parent
    model = ""
    for candidate in (root / "yolov8n-pose.pt", BASE / "yolov8n-pose.pt"):
        if candidate.is_file():
            model = str(candidate)
            break
    if not model:
        errors.append("нет модели yolov8n-pose.pt")

    free_gb = 0.0
    try:
        free_gb = shutil.disk_usage(BASE).free / (1024 ** 3)
    except OSError:
        pass

    return {
        "ok": not errors,
        "python": python,
        "python_exe": sys.executable,
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "модули": modules,
        "модель": model,
        "свободно_гб": round(free_gb, 1),
        "ошибки": errors,
    }

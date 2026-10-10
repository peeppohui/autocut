#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AutoCut Job Runner — мост между панелью UXP и пайплайном на Python.

Панель UXP не умеет запускать процессы и читать их вывод (в UXP есть только
shell.openPath/openExternal — открыть файл, не получить результат). Поэтому
схема такая:

    панель пишет autocut_job.json  ->  запускает run_job.bat  ->  этот скрипт
    выполняет пайплайн  ->  пишет autocut_result.json  ->  панель его читает
    и показывает отчёт.

Запуск вручную (для отладки, без панели):
    python plugin/src/ui/run_job.py --job E:\\AutoCut\\plugin\\autocut_job.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
ROOT = SRC.parents[1]          # корень проекта AutoCut
ML = SRC / "ml"
MODES = SRC / "modes"
IO = SRC / "io"

# Windows не запускает .py сам: нужен интерпретатор. Тот же, что запустил нас.
# Кавычки добавлять нельзя — subprocess на Windows квотит аргументы сам,
# и лишние кавычки становятся частью пути («Access denied»).
PYTHON = sys.executable or "python"


def script(path: Path) -> str:
    """Путь к скрипту. Интерпретатор подставит Step — сам он на Windows
    .py не запускает, а лишние кавычки ломают путь."""
    return str(path)


sys.path.insert(0, str(SRC))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


class Step:
    """Один шаг пайплайна с захватом вывода."""

    def __init__(self, name: str, argv: List[str]):
        self.name = name
        # .py на Windows сам не запускается — подставляем интерпретатор
        argv = list(argv)
        if argv and str(argv[0]).lower().endswith(".py"):
            argv.insert(0, PYTHON)
        self.argv = argv
        self.code = -1
        self.output = ""

    def to_dict(self) -> Dict:
        return {"name": self.name, "code": self.code,
                "output": self.output[-20000:], "argv": self.argv}


def run_step(step: Step, log) -> bool:
    log(f"=== {step.name} ===")
    log("$ " + " ".join(step.argv))
    try:
        p = subprocess.Popen(step.argv, cwd=str(ROOT),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as e:
        step.code = -2
        step.output = f"не найден исполняемый файл: {e}"
        log(step.output)
        return False
    lines = []
    try:
        for raw in p.stdout:
            line = raw.rstrip()
            lines.append(line)
            log("  " + line)
    finally:
        p.wait()
    step.code = p.returncode
    step.output = "\n".join(lines)
    if p.returncode != 0:
        log(f"  ОШИБКА: код {p.returncode}. Дальше не иду.")
        return False
    return True


def build_digest(p: Dict, log) -> List[Step]:
    src = p["input"]
    out = p["out_dir"]
    steps = [Step("1. Определяю планы", [
        script(ML / "detect.py"), "--input", src, "--output", f"{out}/clips.csv",
        "--preview", f"{out}/preview", "--device", p.get("device", "auto")]),
    ]
    args = [script(MODES / "autocut.py"), "--csv", f"{out}/clips.csv",
            "--mode", "digest", "--auto",
            "--out", f"{out}/timeline.csv"]
    if p.get("target_sec"):
        args += ["--target-sec", str(p["target_sec"])]
    if p.get("finale_wide", True):
        args.append("--finale-wide")
    steps.append(Step("2. Раскладываю по склейкам", args))

    music = p.get("music")
    if music:
        steps.append(Step("3. Подложка", [
            script(ML / "music.py"), "--music", music,
            "--duration", str(p.get("target_sec", 60)),
            "--out", f"{out}/bed.wav"]))
    steps.append(Step("4. XML для Premiere", [
        script(IO / "premiere.py"), "--timeline", f"{out}/timeline.csv",
        "--media-dir", src, "--out", f"{out}/ryba.xml",
        "--edl", f"{out}/ryba.edl", "--trim-rec"]))
    return steps


def build_interview(p: Dict, log) -> List[Step]:
    out = p["out_dir"]
    steps = []
    if p.get("sync"):
        steps.append(Step("1. Синхронизация", [
            script(ML / "sync.py"), "--ref", p["ref"], "--cam", p.get("cam", ""),
            "--mic-guest", p.get("mic_guest", ""),
            "--mic-host", p.get("mic_host", ""),
            "--outdir", f"{out}/synced"]))
        wide, guest_cam = f"{out}/synced/ref.mp4", f"{out}/synced/cam.mp4"
        mic_g, mic_h = f"{out}/synced/mic_guest.wav", f"{out}/synced/mic_host.wav"
    else:
        wide, guest_cam = p.get("input", ""), p.get("cam", "")
        mic_g = mic_h = ""

    if guest_cam:
        steps.append(Step("2. Рыба по тексту", [
            script(MODES / "interview.py"), "--wide", wide,
            "--guest-cam", guest_cam, "--guest-mic", mic_g,
            "--host-mic", mic_h, "--outdir", f"{out}/rz",
            "--device", p.get("device", "auto")]))
    else:
        steps.append(Step("2. Рыба по тексту", [
            script(MODES / "interview.py"), "--input", wide,
            "--outdir", f"{out}/rz", "--device", p.get("device", "auto")]))

    args_xml = [script(IO / "premiere.py"), "--timeline", f"{out}/rz/selects.csv",
                "--media-dir", out, "--out", f"{out}/ryba.xml",
                "--edl", f"{out}/ryba.edl", "--trim-rec"]
    if p.get("multicam"):
        # Premiere не собратывает FCP7-multiclip в
        # секвенцию: ложим две дорожки
        args_xml.append("--stacked")
    steps.append(Step("3. XML для Premiere", args_xml))
    steps.append(Step("4. Субтитры", [
        script(IO / "srt.py"), "--selects", f"{out}/rz/selects.csv",
        "--out", f"{out}/ryba.srt"]))
    return steps


def build_news(p: Dict, log) -> List[Step]:
    out = p["out_dir"]
    spec = p.get("spec")
    if not spec:
        raise ValueError("Нужен файл spec (JSON) с описанием материала сюжета")
    spec_path = Path(spec)
    if not spec_path.is_file():
        raise ValueError(f"Не найден spec: {spec_path}")
    return [Step("1. Раскладываю сюжет", [
        script(MODES / "news_package.py"), "--spec", str(spec_path),
        "--outdir", f"{out}/rz"]),
        Step("2. XML для Premiere", [
            script(IO / "premiere.py"), "--timeline", f"{out}/rz/selects.csv",
            "--media-dir", out, "--out", f"{out}/ryba.xml",
            "--edl", f"{out}/ryba.edl", "--trim-rec"]),
        Step("3. Субтитры", [
            script(IO / "srt.py"), "--selects", f"{out}/rz/selects.csv",
            "--out", f"{out}/ryba.srt"]),
    ]


BUILDERS = {"digest": build_digest, "interview": build_interview, "news": build_news}


def validate(job: Dict) -> None:
    mode = job.get("mode")
    if mode not in BUILDERS:
        raise ValueError(f"Неизвестный режим: {mode!r}. Допустимо: "
                         f"{', '.join(sorted(BUILDERS))}")
    out = job.get("out_dir")
    if not out:
        raise ValueError("Не задан out_dir — куда складывать результат")
    p = job.get("params") or {}
    # out_dir живёт в верхнем уровне задания, но билдеры читают его из
    # params — кладём в оба места, чтобы не расходилось
    p["out_dir"] = out
    job["params"] = p
    if mode == "digest":
        if not p.get("input"):
            raise ValueError("Для дайджеста нужна папка с исходниками")
    if mode == "interview":
        has_mic = bool(p.get("mic_guest") or p.get("mic_host"))
        if p.get("sync"):
            if not p.get("ref"):
                raise ValueError("Для синхронизации нужен файл общей камеры")
        elif not (p.get("input") or p.get("ref")):
            raise ValueError("Нужен файл интервью (--input) либо камеры и микрофоны")
        if has_mic and not p.get("sync"):
            log("  ВНИМАНИЕ: микрофоны указаны без синхронизации — "
                "таймкоды не сойдутся. Включи синхронизацию.")
    if mode == "news":
        if not p.get("spec"):
            raise ValueError("Для сюжета нужен файл spec (JSON) — описание "
                             "синхронов, перебивок, закадра и титров")


def collect_artifacts(out_dir: str) -> List[str]:
    """Что получилось на выходе — панель покажет это пользователю."""
    d = Path(out_dir)
    if not d.is_dir():
        return []
    names = ["ryba.xml", "ryba.edl", "ryba.srt", "timeline.csv",
             "clips.csv", "offsets.json", "report.txt"]
    found = [n for n in names if (d / n).is_file()]
    for sub in ("rz", "synced"):
        p = d / sub
        if p.is_dir():
            for f in sorted(p.glob("*.csv")):
                found.append(f"{sub}/{f.name}")
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AutoCut — запуск задания из панели")
    ap.add_argument("--job", required=True, help="путь к autocut_job.json")
    ap.add_argument("--result", default="", help="куда писать результат "
                                                "(по умолчанию — рядом с job)")
    args = ap.parse_args(argv)

    job_path = Path(args.job)
    result_path = Path(args.result) if args.result else \
        job_path.parent / "autocut_result.json"

    started = time.strftime("%Y-%m-%d %H:%M:%S")
    lines: List[str] = []

    def log(msg: str = ""):
        lines.append(msg)
        try:
            print(msg, flush=True)
        except Exception:
            pass

    log(f"AutoCut, задание от {started}")
    result = {"status": "error", "mode": "", "started": started,
              "finished": "", "steps": [], "artifacts": [], "log": lines}

    try:
        job = json.loads(job_path.read_text(encoding="utf-8-sig"))
    except Exception as e:
        log(f"Не разобрал задание: {e}")
        result["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                               encoding="utf-8")
        return 2

    mode = job.get("mode", "")
    result["mode"] = mode
    log(f"Режим: {mode}")

    try:
        validate(job)
        out_dir = Path(job["out_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        log(f"Результат в: {out_dir}")
        log(f"Параметры: {json.dumps(job.get('params') or {}, ensure_ascii=False)}")
        log("")

        steps = BUILDERS[mode](job["params"], log)
        ok = True
        for st in steps:
            if not run_step(st, log):
                ok = False
                break
            log("")
        result["steps"] = [s.to_dict() for s in steps]
        result["status"] = "ok" if ok else "error"
    except NotImplementedError as e:
        log(str(e))
        result["status"] = "not_implemented"
    except Exception as e:
        log(f"Ошибка задания: {type(e).__name__}: {e}")
        result["status"] = "error"

    result["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    result["artifacts"] = collect_artifacts(job.get("out_dir", ""))
    try:
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                               encoding="utf-8")
        log(f"Результат записан: {result_path}")
    except Exception as e:
        log(f"Не смог записать результат: {e}")
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
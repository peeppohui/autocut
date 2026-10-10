#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoCut Review — разбор ГОТОВОГО ролика без исходников.
Режет видео по склейкам, классифицирует каждый план (деталь/крупный/
средний1/средний2/общий/дальний) и проверяет монтаж по правилам крупности.

Использование:
  python review.py --input готовый_ролик.mp4 --outdir разбор_ролика
  # получится: shots.csv (план каждого куска), sheet.jpg (контакт-лист
  # с подписями для быстрой ручной проверки), report.txt (оценка монтажа)

Нужно один раз: python -m pip install ultralytics opencv-python
"""

from __future__ import annotations
import argparse
import csv
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bootstrap  # noqa: F401  — регистрирует src/modes, src/ml и др. в sys.path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import cv2

import detect
from detect import classify_by_pose
from autocut import Clip, validate_sequence

THUMB_W, THUMB_H, COLS = 320, 180, 6


def scene_bounds(video: Path, threshold: float = 0.35, min_shot: float = 0.6):
    """Границы планов по детектору смен сцен ffmpeg. Возвращает [(t0,t1), ...]."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(video),
         "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True)
    cuts = sorted(float(m) for m in re.findall(r"pts_time:([\d.]+)", proc.stderr))
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True).stdout.strip())
    bounds, start, segs = [0.0] + cuts + [dur], 0.0, []
    for b in bounds[1:]:
        if b - start < min_shot:
            continue
        segs.append((round(start, 2), round(b, 2)))
        start = b
    if start < bounds[-1] - 0.3:
        segs.append((round(start, 2), round(bounds[-1], 2)))
    return segs, dur


def classify_frame(frame, model, conf: float):
    """Один кадр -> FrameVerdict."""
    h, w = frame.shape[:2]
    res = model.predict(frame, conf=conf, verbose=False,
                          device=getattr(model, "_autocut_device", "cpu"))[0]
    n = len(res.boxes) if res.boxes is not None else 0
    if n == 0:
        return classify_by_pose(w, h, None, None, None, 0)
    xywhn = res.boxes.xywhn.cpu().numpy()
    areas = res.boxes.xywh.cpu().numpy()[:, 2] * res.boxes.xywh.cpu().numpy()[:, 3]
    bi = int(areas.argmax())
    b = tuple(float(x) for x in xywhn[bi])
    if res.keypoints is not None and res.keypoints.xy is not None and len(res.keypoints.xy) > bi:
        kx = res.keypoints.xy.cpu().numpy()[bi]
        kc = (res.keypoints.conf.cpu().numpy()[bi]
              if res.keypoints.conf is not None else [1.0] * 17)
        kl = [[float(x), float(y)] for x, y in kx]
        cl = [float(c) for c in kc]
    else:
        kl, cl = None, None
    return classify_by_pose(w, h, b, kl, cl, n)


def letterbox(frame, w: int = THUMB_W, h: int = THUMB_H):
    scale = min(w / frame.shape[1], h / frame.shape[0])
    nw, nh = max(1, int(frame.shape[1] * scale)), max(1, int(frame.shape[0] * scale))
    small = cv2.resize(frame, (nw, nh))
    top = (h - nh) // 2
    left = (w - nw) // 2
    return cv2.copyMakeBorder(small, top, h - nh - top, left, w - nw - left,
                              cv2.BORDER_CONSTANT, value=(0, 0, 0))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AutoCut Review — разбор готового ролика")
    ap.add_argument("--input", required=True, help="готовый ролик (mp4/mov/...)")
    ap.add_argument("--outdir", default="разбор", help="папка результата")
    ap.add_argument("--threshold", type=float, default=0.35, help="чувствительность поиска склеек")
    ap.add_argument("--conf", type=float, default=0.35, help="порог детекции людей")
    ap.add_argument("--model", default="yolov8n-pose.pt")
    ap.add_argument("--device", default="auto", help="auto|cpu|cuda (auto: GPU если завелась)")
    args = ap.parse_args(argv)

    video = Path(args.input)
    if not video.is_file():
        print(f"Не найден видеофайл: {video}")
        return 2
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)

    segs, dur = scene_bounds(video, args.threshold)
    print(f"Видео {dur:.0f}с, найдено планов: {len(segs)}")
    model = detect.load_yolo(args.model, args.device)

    cap = cv2.VideoCapture(str(video))
    rows, thumbs = [], []
    for i, (t0, t1) in enumerate(segs):
        mid = (t0 + t1) / 2
        votes = []
        for dt in (-0.2, 0.0, 0.2):
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, mid + dt) * 1000)
            ok, fr = cap.read()
            if ok and fr is not None:
                votes.append(classify_frame(fr, model, args.conf))
        if not votes:
            continue
        plan = Counter(v.plan for v in votes).most_common(1)[0][0]
        hit = [v for v in votes if v.plan == plan]
        conf = sum(v.conf for v in hit) / len(hit)
        ppl = max(v.people for v in votes)
        expl = Counter(v.detail for v in hit).most_common(1)[0][0]
        if len(hit) * 2 <= len(votes) + 1:
            conf = min(conf, 0.65)
            expl += " Голоса кадров разделились почти поровну — проверьте по превью."
        rows.append({"n": i, "t0": t0, "t1": t1, "dur": round(t1 - t0, 1),
                     "plan": plan, "conf": round(conf, 2), "people": ppl, "explain": expl})
        cap.set(cv2.CAP_PROP_POS_MSEC, mid * 1000)
        ok, fr = cap.read()
        if ok and fr is not None:
            th = letterbox(fr)
            cv2.rectangle(th, (0, 0), (THUMB_W, 26), (0, 0, 0), -1)
            cv2.putText(th, f"{i} {t0}-{t1}s {plan} {conf:.2f}", (6, 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1)
            thumbs.append(th)
    cap.release()

    with open(out / "shots.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["n", "t0", "t1", "dur", "plan", "conf", "people", "explain"])
        w.writeheader()
        w.writerows(rows)

    grid = []
    for g in range(0, len(thumbs), COLS):
        row = thumbs[g:g + COLS]
        while len(row) < COLS:
            import numpy as np
            row.append(np.zeros((THUMB_H, THUMB_W, 3), dtype="uint8"))
        grid.append(cv2.hconcat(row))
    if grid:
        cv2.imwrite(str(out / "sheet.jpg"), cv2.vconcat(grid))

    # строгая проверка (все куски — один герой: честно завышает строгость,
    # т.к. не знает, где разные люди; одинаковые планы разных героев — норма)
    clips = [Clip(f"seg{r['n']}.mp4", r["plan"], "кадр", r["dur"]) for r in rows]
    rep = validate_sequence(clips, mode="digest")
    avg_shot = dur / len(rows) if rows else 0
    dist = dict(Counter(r["plan"] for r in rows))

    lines = [
        f"Разбор: {video.name} ({dur:.0f}с, {len(rows)} планов, средний план {avg_shot:.1f}с)",
        f"Набор крупностей: {dist}",
        f"Строгая оценка склеек: {rep['total']} / 100 — {rep['grade']}",
        "(строгость завышена: программа не знает героев, одинаковые планы разных людей — норма)",
        "",
        "Критично (проверить вручную):",
        *[f"  [X] {p}" for p in rep["problems"][:15]],
        "Замечания:",
        *[f"  [!] {w}" for w in rep["warnings"][:15]],
        "",
        "Планы к проверке по sheet.jpg:",
        "ОБЯЗАТЕЛЬНО (уверенность < 0.65 — детект не уверен):",
        *[f"  {r['n']}: {r['t0']}-{r['t1']}с -> {r['plan']} ({r['conf']}) — {r['explain']}"
          for r in rows if r["conf"] < 0.65],
        "БЕГЛО ВЗГЛЯНУТЬ (0.65 — толпа/спорные голоса, обычно верно, но подтвердите):",
        *[f"  {r['n']}: {r['t0']}-{r['t1']}с -> {r['plan']} — {r['explain']}"
          for r in rows if r["conf"] == 0.65],
    ]
    (out / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:12]))
    print(f"\nГотово: {out} (shots.csv, sheet.jpg, report.txt)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""Просмотр результата авто-монтажа, не заходя в Premiere.

Две вещи, и обе экономят больше всего времени:

1. Превью-кадр каждого куска. Листаешь сетку и сразу видишь, где
   авто-монтаж промахнулся с кадром, не открывая Premiere.
2. Готовая рыба одним файлом со звуком. Собранный mp4 открывается
   системным плеером, и рез слышно целиком - с микрофонами, в том
   порядке, в каком он пойдёт в монтаж.

Видео и звук склеиваются отдельно: в selects.csv картинка берётся из
камеры, а звук - из микрофонной дорожки. Общим списком их не
собрать, потому что это разные файлы с разными таймкодами.
"""
from __future__ import annotations

import csv
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

# Кадр берём из середины куска: в начале и конце чаще всего зачин
# или обрыв, а в середине как раз то, что попадёт в эфир.
THUMB_W = 384
THUMB_H = 216


def load_selects(path) -> List[Dict]:
    """Кусы из selects.csv. Без in/out (перебивки) пропускаем."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        try:
            i, o = float(r.get("in") or ""), float(r.get("out") or "")
        except ValueError:
            continue
        if o <= i or not (r.get("path") or "").strip():
            continue
        r["in"], r["out"] = i, o
        out.append(r)
    return out


def thumb_name(row: Dict, fps: int = 25) -> str:
    """Имя файла превью. Зависит от файла, точек и размера - сменили
    нарезку, кэш пересоздаётся сам."""
    src = row.get("path", "")
    try:
        st = Path(src).stat()
        key = f"{src}|{st.st_size}|{row['in']:.2f}|{row['out']:.2f}"
    except OSError:
        key = f"{src}|{row['in']:.2f}|{row['out']:.2f}"
    return "t_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:14] + ".png"


def grab_frame(video, t: float, dst: Path):
    """Один кадр из видео в PNG. None, если не вышло."""
    import cv2
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return None
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round(t * fps))))
        ok, frame = cap.read()
        if not ok:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
        if not ok:
            return None
        h, w = frame.shape[:2]
        scale = THUMB_W / float(w or 1)
        frame = cv2.resize(frame, (THUMB_W, max(1, int(h * scale))))
        dst.parent.mkdir(parents=True, exist_ok=True)
        # cv2.imwrite на Windows не понимает кир��ллицу в пути и молча
        # ничего не пишет. Кодируем картинку в память и сохраняем файлом
        # средствами Python - путь тогда любой.
        ok, buf = cv2.imencode(".png", frame)
        if not ok:
            return None
        dst.write_bytes(buf.tobytes())
        return dst
    finally:
        cap.release()


def make_thumbs(selects, out_dir, fps: int = 25) -> List[Dict]:
    """Превью-кадр для каждого куска. Возвращает куски с полем thumb."""
    out_dir = Path(out_dir)
    rows = load_selects(selects) if isinstance(selects, (str, Path)) \
        else list(selects)
    made = 0
    for r in rows:
        dst = out_dir / thumb_name(r, fps)
        r["thumb"] = str(dst) if dst.is_file() else None
        if r["thumb"]:
            continue
        mid = (r["in"] + r["out"]) / 2.0
        if grab_frame(r["path"], mid, dst):
            r["thumb"] = str(dst)
            made += 1
    return rows


# ----------------------------------------------------------------- сборка
def _ffconcat(rows: List[Dict], path_key: str, dest: Path) -> Path:
    """Список файлов для конкатенатора ffmpeg.

    Имя файла задаёт вызывающий: два списка должны лежать рядом, но
    различаться, иначе второй затрёт первый и видео соберётся из
    звуковых файлов - ffmpeg ответит «нет потоков».
    """
    txt = ["ffconcat version 1.0"]
    for r in rows:
        src = (r.get(path_key) or r["path"])
        p = Path(src).absolute().as_posix().replace("'", r"'\''")
        txt.append(f"file '{p}'")
        txt.append(f"inpoint {r['in']:.3f}")
        txt.append(f"outpoint {r['out']:.3f}")
        txt.append(f"duration {r['out'] - r['in']:.3f}")
    dest = Path(dest)
    dest.write_text("\n".join(txt) + "\n", encoding="utf-8")
    return dest


def make_preview(selects, out_mp4, fps: int = 25) -> Dict:
    """Склеивает рыбу в один mp4: картинка из камер, звук из микрофонов."""
    rows = load_selects(selects) if isinstance(selects, (str, Path)) \
        else [r for r in selects if r.get("in")]
    if not rows:
        return {"ok": False, "error": "в selects.csv нет кусков с таймкодом"}

    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_mp4.parent / "_preview_tmp"
    tmp.mkdir(exist_ok=True)
    v_only, a_only = tmp / "v.mp4", tmp / "a.m4a"

    vlist = _ffconcat(rows, "path", tmp / "video.ffconcat")
    # звук: у куска либо свой микрофон, либо камера
    arows = [r for r in rows if (r.get("audio") or "").strip()] or rows
    alist = _ffconcat(arows, "audio" if arows[0].get("audio") else "path",
                      tmp / "audio.ffconcat")

    def run(cmd):
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")

    # куски склеены из разных файлов, поэтому таймкоды на стыках рвутся;
    # -fps_mode cfr пересобирает их ровно, иначе ffmpeg ругается на
    # немонотонные метки времени
    r1 = run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
              "-i", str(vlist), "-an", "-c:v", "libx264", "-preset", "veryfast",
              "-crf", "23", "-pix_fmt", "yuv420p", "-fps_mode", "cfr",
              "-r", str(fps), str(v_only)])
    if r1.returncode:
        return {"ok": False, "error": (r1.stderr or "").strip()[-400:]}

    r2 = run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
              "-i", str(alist), "-vn", "-c:a", "aac", "-b:a", "160k",
              "-ar", "48000", str(a_only)])
    if r2.returncode:
        return {"ok": False, "error": (r2.stderr or "").strip()[-400:]}

    r3 = run(["ffmpeg", "-y", "-v", "error", "-i", str(v_only),
              "-i", str(a_only), "-c:v", "copy", "-c:a", "copy",
              "-shortest", str(out_mp4)])
    if r3.returncode:
        return {"ok": False, "error": (r3.stderr or "").strip()[-400:]}

    dur = sum(r["out"] - r["in"] for r in rows)
    return {"ok": True, "path": str(out_mp4), "clips": len(rows),
            "duration": round(dur, 1)}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="AutoCut — превью рыбы")
    ap.add_argument("--selects", required=True)
    ap.add_argument("--thumbs", default="", help="папка для превью-кадров")
    ap.add_argument("--out", default="preview.mp4")
    ap.add_argument("--fps", type=int, default=25)
    a = ap.parse_args()
    if a.thumbs:
        rows = make_thumbs(a.selects, a.thumbs, a.fps)
        got = sum(1 for r in rows if r.get("thumb"))
        print(f"превью-кадров: {got} из {len(rows)} -> {a.thumbs}")
    res = make_preview(a.selects, a.out, a.fps)
    print(res if res.get("ok") else "ОШИБКА: " + res.get("error", ""))
    sys.exit(0 if res.get("ok") else 1)
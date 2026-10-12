#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Вертикальная версия готового монтажа: 9:16 для Reels и Shorts.

Что делает. Берёт уже собранный selects.csv и переделывает каждый
видеокусок в вертикальный: центр кадра вырезается по 9:16 и
растягивается до 1080x1920. На выходе - новая раскладка с путями на
вертикальные файлы, её и скармливаем premiere.py.

Почему через ffmpeg, а не кадрированием в XML. Кадрирование в FCP7
делается вложенным clipitem через clipref, и Premiere не гарантирует,
что читает это как «заполнить по центру» - а нечитает он молча, и
импорт падает целиком. Тот же класс граблей уже стоил четырех попыток
с <multiclip>. Здесь путь другой: XML собирается ровно так же, как
уже умеет, а кадр меняется на диске.

Почему это не портит оригиналы и не жрёт два часа перекодирования.
Перекодируются только куски МОНТАЖА, а не исходники: из двухчасового
интервью в вертикаль уходит ровно столько, сколько длится рыба.

Звук не трогаем: колонка audio указывает на отдельные микрофоны, резать
там нечего, пробегать их заново не нужно.
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bootstrap  # noqa: F401  — регистрирует src/config в sys.path
import faces

VIDEO_EXT = (".mp4", ".mov", ".mxf", ".mkv")

# есть ли звук в исходнике — по одному и тому же файлу это не спрашивают заново
_AUDIO_CACHE: Dict[str, bool] = {}


def crop_filter(width: int, height: int, speed: float = 1.0,
                box=None) -> str:
    """Обрезка под 9:16 с последующим масштабированием.

    Ширина реза считается от высоты, а не наоборот: вертикаль получается
    всегда одного кадра, даже если исходник шире или уже 16:9. Делим на
    2, потому что libx264 требует чётные размеры, а 1080*9/16 = 607.5.
    Ускорение через setpts делается здесь же, чтобы не считать
    длительность руками.

    box - окно (x, y, w, h) от faces.crop_window. Без него режем
    геометрический центр: так вертикаль работала до появления слежения
    за лицом, и этим же путём идёт любой кусок, где лица не нашлось.
    """
    if box:
        bx, by, bw, bh = (int(v) for v in box)
        vf = f"crop={bw}:{bh}:{max(0, bx)}:{max(0, by)}"
    else:
        vf = "crop=trunc(ih*9/16/2)*2:ih"
    vf += f",scale={width}:{height}"
    # любое значение, отличное от 1.0, уважаем буквально: если человек
    # написал 1.01 - он хотел ускорить на процент, а не «примерно как
    # есть». Бессмысленные значения (0 или меньше) просто игнорируем.
    if speed > 0 and speed != 1.0:
        vf += f",setpts={1.0 / speed:.6f}*PTS"
    return vf


def clip_duration(row: Dict) -> float:
    """Длительность куска. Сначала in/out, и только если их нет - колонка."""
    try:
        a, b = float(row.get("in") or ""), float(row.get("out") or "")
        if b > a:
            return b - a
    except ValueError:
        pass
    try:
        return float(row.get("duration") or 0)
    except ValueError:
        return 0.0


def atempo_chain(speed: float) -> str:
    """Ускорение звука. atempo понимает только 0.5..100, поэтому
    необычные значения разбиваются на цепочку."""
    if speed <= 0 or speed == 1.0:
        return ""
    parts, rest = [], float(speed)
    while rest < 0.5:
        parts.append(0.5)
        rest /= 0.5
    while rest > 100.0:
        parts.append(100.0)
        rest /= 100.0
    parts.append(rest)
    return ",".join(f"atempo={p:.4f}" for p in parts)


def _has_audio(path: Path) -> bool:
    """Есть ли в файле звуковая дорожка. Проверка кэшируется: один и тот
    же исходник встречается в монтаже десятками кусков."""
    key = str(path)
    if key in _AUDIO_CACHE:
        return _AUDIO_CACHE[key]
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    has = "audio" in (proc.stdout or "")
    _AUDIO_CACHE[key] = has
    return has


def cut_one(src: Path, dst: Path, row: Dict, width: int, height: int,
            speed: float, box=None, audio: str = "") -> str:
    """Один кусок в вертикаль. Возвращает "" или текст ошибки.

    Порядок аргументов важен: сначала все входы (-i), потом выходные
    опции. Стоит поставить -vf между двумя -i, ffmpeg ругается
    «Option vf cannot be applied to input» и уходит с кодом -22.

    Про звук. Раньше внешний микрофон подключался вторым входом БЕЗ
    -ss/-t, то есть звук брался с начала файла, а не с начала куска.
    На двухминутной рыбе это расходилось на секунды, и реплика уезжала
    из-под картинки. Теперь у обоих входов одинаковое окно.

    Звук уезжает внутрь файла, а колонка audio после этого чистится.
    Причина в том, что Premiere берёт in/out у клипа и для картинки, и
    для звука: оставив колонку audio, мы бы заставили его искать
    обрезанный кусок в полном микрофоне.
    """
    dur = clip_duration(row)
    if dur <= 0.1:
        return "у куска нет длительности"
    t0 = max(0.0, float(row.get("in") or 0))
    ext = Path(audio) if (audio and Path(audio).is_file()) else None
    src_audio = ext if ext else (src if _has_audio(src) else None)

    win = ["-ss", f"{t0:.3f}", "-t", f"{dur:.3f}"]
    inputs = win + ["-i", str(src)]
    if ext:
        inputs += win + ["-i", str(ext)]

    output = ["-vf", crop_filter(width, height, speed, box)]
    if src_audio is not None:
        output += ["-map", "0:v:0", "-map", "1:a:0" if ext else "0:a:0",
                   "-c:a", "aac", "-b:a", "192k"]
        af = atempo_chain(speed)
        if af:
            output += ["-af", af]
    else:
        output += ["-an"]
    output += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-pix_fmt", "yuv420p", "-shortest"]

    proc = subprocess.run(["ffmpeg", "-y", "-v", "error"] + inputs
                          + output + [str(dst)],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not dst.is_file() or dst.stat().st_size == 0:
        # молчать нельзя: иначе кусок просто исчезнет из монтажа
        err = (proc.stderr or "").strip().splitlines()
        return err[-1] if err else f"ffmpeg код {proc.returncode}"
    return ""


def plan_crop(path: Path, row: Dict, aspect: float, model):
    """Окно кропа под лицо для одного куска.

    Возвращает (box, причина). box=None - режем по центру, и тогда
    причина объясняет почему: кусок без лица виден редактору, а не
    остаётся загадкой в финальном ролике.
    """
    t0 = max(0.0, float(row.get("in") or 0))
    t1 = t0 + clip_duration(row)
    samples = faces.scan_segment(path, t0, t1, model)
    face = faces.main_face_of_segment(samples, faces.MIN_FACE_W)
    if face is None:
        return None, "лица не нашлось - кроп по центру"
    sw, sh = faces.source_size(path)
    if sw <= 0 or sh <= 0:
        return None, "не удалось прочитать размер кадра"
    return faces.crop_window(face, sw, sh, aspect), ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Вертикальная (9:16) версия готового монтажа")
    ap.add_argument("--selects", required=True, help="исходный selects.csv")
    ap.add_argument("--outdir", required=True, help="куда сложить вертикаль")
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--speed", type=float, default=1.0,
                    help="ускорение, 1.5 = в полтора раза быстрее")
    ap.add_argument("--no-face", action="store_true",
                    help="резать по центру, не искать лицо (быстрее)")
    ap.add_argument("--device", default="auto")
    args = ap.parse_args(argv)

    src_csv = Path(args.selects)
    if not src_csv.is_file():
        print(f"Не найден selects: {src_csv}")
        return 2
    out_dir = Path(args.outdir)
    (out_dir / "clips").mkdir(parents=True, exist_ok=True)

    with open(src_csv, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]

    aspect = args.width / max(1, args.height)
    model = None
    if not args.no_face and faces.ENABLED:
        print("Ищу лица, чтобы кроп не резал говорящего...")
        try:
            import detect
            model = detect.load_yolo(faces.MODEL, args.device)
        except Exception as e:
            # слежение - улучшение, а не условие сборки: без него вертикаль
            # просто получится геометрически центрированной
            print(f"  слежение за лицом отключено: {str(e)[:160]}")
            model = None

    out_rows: List[Dict] = []
    cut = skipped = followed = 0
    alt_cut = 0
    for i, row in enumerate(rows):
        new = dict(row)
        track = (row.get("track") or "V1").strip().upper()
        src = (row.get("path") or "").strip()
        is_video = track.startswith("V") and Path(src).is_file() \
            and Path(src).suffix.lower() in VIDEO_EXT
        if not is_video:
            # самостоятельные звуковые дорожки и второй ракурс не трогаем
            out_rows.append(new)
            continue
        name = f"clip{i:04d}.mp4"
        dst = out_dir / "clips" / name
        speed = args.speed if track.upper() == "V1" else 1.0
        audio = (row.get("audio") or "").strip()

        box, why = (None, "слежение выключено")
        if model is not None:
            try:
                box, why = plan_crop(Path(src), row, aspect, model)
            except Exception as e:
                box, why = None, f"сбой слежения: {str(e)[:80]}"
        if box is not None:
            followed += 1

        err = cut_one(Path(src), dst, row, args.width, args.height, speed,
                      box, audio)
        if not err:
            dur_out = clip_duration(row) / speed
            new["path"] = str(dst)
            new["file"] = name
            new["duration"] = f"{dur_out:.3f}"
            # файл уже нарезан, поэтому кусок начинается с нуля. Если
            # оставить исходные in/out, premiere.py возьмёт их как есть
            # (он проверяет их раньше длительности) и вырежет кадр
            # не с того места.
            new["in"] = "0.000"
            new["out"] = f"{dur_out:.3f}"
            # звук уехал внутрь файла вместе с картинкой
            new["audio"] = ""
            cut += 1
            if why and box is None:
                print(f"  кусок {i}: {why}")
        else:
            # кусок оставляем горизонтальным и говорим почему: иначе в
            # вертикальном ролике будет зелёная полоса без объяснений
            skipped += 1
            print(f"  кусок {i} ({row.get('file')}) оставлен как есть: {err}")
            out_rows.append(new)
            continue

        # Запасной ракурс. Он лежит в колонках alt/alt_path, и premiere.py
        # кладёт его на V2 с ТЕМ ЖЕ in/out, что и основной клип. Значит
        # перерезать его нужно так же, иначе вторая дорожка останется
        # горизонтальной, а ролик поедет при переключении глазком.
        alt_src = (row.get("alt_path") or "").strip()
        if alt_src and Path(alt_src).is_file() \
                and Path(alt_src).suffix.lower() in VIDEO_EXT:
            alt_name = f"clip{i:04d}_alt.mp4"
            alt_dst = out_dir / "clips" / alt_name
            alt_box = None
            if model is not None:
                try:
                    alt_box, _ = plan_crop(Path(alt_src), row, aspect, model)
                except Exception:
                    alt_box = None
            alt_err = cut_one(Path(alt_src), alt_dst, row, args.width,
                              args.height, 1.0, alt_box, "")
            if not alt_err:
                new["alt"] = alt_name
                new["alt_path"] = str(alt_dst)
                alt_cut += 1
            else:
                # без второго ракурса ролик всё равно собирается, но
                # монтажёр должен знать, что переключаться будет некуда
                new["alt"] = ""
                new["alt_path"] = ""
                print(f"  кусок {i}: запасной ракурс не перерезан ({alt_err})")
        out_rows.append(new)

    out_csv = out_dir / "selects.csv"
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in out_rows:
            w.writerow({k: r.get(k, "") for k in fields})

    print(f"Вертикаль готова: {cut} кусков, пропущено {skipped}")
    if alt_cut:
        print(f"  запасных ракурсов перерезано: {alt_cut}")
    if model is not None:
        print(f"  кроп по лицу: {followed} из {cut}, остальное - по центру")
    else:
        print("  кроп по центру (слежение за лицом выключено)")
    print(f"  {args.width}x{args.height}"
          + (f", скорость x{args.speed}" if args.speed != 1.0 else ""))
    print(f"  {out_csv}")
    print("Дальше: premiere.py --timeline "
          f"{out_csv} --width {args.width} --height {args.height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
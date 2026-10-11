#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AutoCut SRT — субтитры для Premiere.

Premiere читает SRT нативно: File > Import (или монтажная панель -> Субтитры).
Формат: HH:MM:SS,mmm --> HH:MM:SS,mmm, номер, текст, пуста�� строка.

Важно про таймкод: в SRT всегда реальное время, drop-frame не применяется.
Таймкод 00:00:59:29 при 29.97 DF и при 29.97 NDF — это РАЗНЫЕ моменты,
но в SRT оба пишутся как 00:00:59,767. Поэтому тайминги берём в секундах
и не пересчитываем через кадры.

Использование:
  python srt.py --selects rz/selects.csv --out ryba.srt
  python srt.py --json rz/speech.json --out ryba.srt
  python srt.py --selects rz/selects.csv --out ryba.srt --clean
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bootstrap  # noqa: F401  — регистрирует src/config в sys.path
import settings

# Метки, которые interview.py ставит в desc: [?] короткий кусок,
# [~] спорный по микрофонам. В субтитрах они лишние.
FLAG_RX = re.compile(r"^\s*[\[~?\]]+\s*")
SPEAKER_RX = re.compile(r"^\s*(ГОСТЬ|ВЕДУЩИЙ|ГOST|HOST)\s*:\s*", re.IGNORECASE)

# Служебные подписи таймлайна: попадают в desc, но не являются речью.
# В субтитры им нельзя.
SERVICE_LABELS = {
    "закадр", "перебивка", "vo", "broll", "перебивки", "закадры",
    "стендап", "лид", "суть", "детали",
}


def fmt_time(seconds: float) -> str:
    """Секунды -> HH:MM:SS,mmm. Отрицательное и нечисловое значение даёт 00:00:00,000."""
    try:
        total_ms = int(round(float(seconds) * 1000))
    except (TypeError, ValueError):
        return "00:00:00,000"
    if total_ms < 0:
        total_ms = 0
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def clean_text(text: str, drop_speaker: bool = True) -> str:
    """Убирает служебные метки и имя спикера."""
    t = FLAG_RX.sub("", text or "").strip()
    if drop_speaker:
        t = SPEAKER_RX.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def build_srt(segments: List[Dict], *, drop_speaker: bool = True,
              min_len: float = 0.2) -> str:
    """segments: [{start, end, text}] в секундах. Возвращает текст SRT."""
    blocks: List[Dict] = []
    for seg in segments:
        try:
            start = float(seg["start"])
            end = float(seg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = clean_text(seg.get("text", ""), drop_speaker)
        if not text:
            continue
        if end < start:
            start, end = end, start
        if end - start < min_len:
            continue
        # Планировщик может разрезать одну реплику на куски, чтобы влезть
        # в хронометраж блока. В субтитрах такая реплика должна быть одна,
        # иначе фраза повторится три раза подряд.
        if blocks and blocks[-1]["text"] == text and \
                abs(start - blocks[-1]["end"]) < 0.05:
            blocks[-1]["end"] = end
            continue
        blocks.append({"start": start, "end": end, "text": text})

    out: List[str] = []
    for i, b in enumerate(blocks, 1):
        out.append(str(i))
        out.append(f"{fmt_time(b['start'])} --> {fmt_time(b['end'])}")
        out.append(b["text"])
        out.append("")
    return "\n".join(out)


def segments_from_selects(path: Path, *, drop_speaker: bool = True) -> List[Dict]:
    """Достаёт куски с текстом из selects.csv (то, что делает interview.py).

    Время берём НЕ из колонок in/out. Там лежит время внутри исходного
    файла, а Premiere сжимает паузы и собирает монтаж встык. Позиция
    субтитра в секвенции равна сумме длительностей предыдущих кусков -
    именно так premiere.py раскладывает таймлайн.

    Текст берём из колонки text, если она есть (news_package кладёт туда
    реальные слова речи), иначе из desc. Служебные подписи вроде
    «закадр» или «перебивка» в субтитры не идут.
    """
    rows: List[Dict] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = {(k or "").strip().lower(): (v or "").strip()
                   for k, v in row.items()}
            if not row.get("file"):
                continue
            raw = row.get("text") or row.get("desc") or ""
            if not raw:
                continue
            try:
                dur = float(row.get("duration") or "")
            except ValueError:
                try:
                    dur = float(row.get("out") or "") - float(row.get("in") or "")
                except ValueError:
                    continue
            if dur <= 0:
                continue
            rows.append({"dur": dur, "raw": raw, "has_text": bool(row.get("text"))})

    out: List[Dict] = []
    cursor = 0.0
    for r in rows:
        start = cursor
        cursor += r["dur"]
        text = clean_text(r["raw"], drop_speaker)
        if not text:
            continue
        # если это служебная подпись без речи, субтитровать нечего
        if not r["has_text"] and text.lower() in SERVICE_LABELS:
            continue
        out.append({"start": start, "end": cursor, "text": text})
    return out


def segments_from_json(path: Path) -> List[Dict]:
    """Из speech.json: там только интервалы, без текста."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Ожидался список интервалов в верхнем уровне JSON")
    return [{"start": d.get("start"), "end": d.get("end"), "text": d.get("text", "")}
            for d in data if isinstance(d, dict)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AutoCut SRT — субтитры для Premiere")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--selects", help="selects.csv из interview.py (есть текст)")
    src.add_argument("--json", help="speech.json: только интервалы исходника, "
                                   "текста нет - субтитры не собрать")
    ap.add_argument("--out", default="subtitles.srt", help="выходной SRT")
    ap.add_argument("--keep-speaker", action="store_true",
                    help="не убирать имя спикера из текста")
    ap.add_argument("--max-chars", type=int, default=0,
                    help="разбивать длинные реплики на строки по N символов (0 = нет)")
    args = ap.parse_args(argv)

    src_path = Path(args.selects or args.json)
    if not src_path.is_file():
        print(f"Не найден файл: {src_path}")
        return 2

    try:
        if args.selects:
            segs = segments_from_selects(src_path, drop_speaker=not args.keep_speaker)
        else:
            segs = segments_from_json(src_path)
    except Exception as e:
        print(f"Не разобрал {src_path}: {e}")
        return 1

    if not segs:
        print("Готово нечего: ни одной реплики с текстом.")
        print("Если брал speech.json — там только тайминги, нужен selects.csv.")
        return 1

    # speech.json хранит одни интервалы, текста в нём нет, и build_srt
    # молча выкидывал такие реплики. На выходе оказывался пустой файл,
    # а инструмент рапортовал «сохранено, 73 реплик». Проверяем текст
    # ДО записи: пустой SRT хуже, чем явная ошибка.
    if not any((s.get("text") or "").strip() for s in segs):
        print("В файле нет текста реплик — субтитры собрать не из чего.")
        print("Бери selects.csv:")
        print("  python -m src.io.srt --selects selects.csv --out ryba.srt")
        return 1

    max_chars = int(settings.get("export.srt_max_chars", 0)) or args.max_chars
    if max_chars:
        for s in segs:
            s["text"] = wrap_text(s["text"], max_chars)

    text = build_srt(segs, drop_speaker=not args.keep_speaker)
    if not text.strip():
        print("После сборки не осталось ни одного блока — проверь тайминги.")
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8-sig")

    n_blocks = text.count("-->")
    last = max((float(s["end"]) for s in segs), default=0.0)
    print(f"SRT сохранён: {out} ({n_blocks} реплик, до {fmt_time(last)}).")
    print("Premiere: File > Import, либо монтажная панель > Субтитры > импорт.")
    return 0


def wrap_text(text: str, width: int) -> str:
    """Перенос по словам с сохранением переносов строк."""
    lines: List[str] = []
    for para in (text or "").split("\n"):
        cur = ""
        for word in para.split():
            if not cur:
                cur = word
            elif len(cur) + 1 + len(word) <= width:
                cur += " " + word
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
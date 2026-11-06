# -*- coding: utf-8 -*-
"""Распознать файлы рекордера и положить в кеш.

Рекордер пишет звук всего мероприятия, и в нём лежит та реплика,
которую монтажёр кладёт на отдельную звуковую дорожку: стендап и
ответы интервью. Какая запись соответствует какой камере, решается
по словам — так надёжнее, чем по огибающей: у камеры, снятой без
микрофона, голоса нет вовсе, и слышно только зал.

Запуск: `python -m src.ml.recvoice <папка звк> <папка кеша>`
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SRC / "config") not in sys.path:
    sys.path.insert(0, str(SRC / "config"))

import bootstrap            # noqa: E402,F401
import speech               # noqa: E402
from modes.recsound import is_rec_file  # noqa: E402


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("нужно: папка с файлами рекордера и папка кеша")
        return 1
    src = Path(argv[0])
    work = Path(argv[1]) if len(argv) > 1 else Path("_work") / "rec"
    work.mkdir(parents=True, exist_ok=True)
    model = argv[2] if len(argv) > 2 else "large-v3"
    files = sorted(p for p in src.iterdir() if is_rec_file(p))
    print("Файлов рекордера: {}".format(len(files)))
    print("Кеш: {}".format(work))
    for i, p in enumerate(files, 1):
        print("[{}/{}] {}".format(i, len(files), p.name), flush=True)
        try:
            words = speech.words_of(p, work, model, "auto")
        except Exception as e:
            print("  не разобрать: {}".format(str(e)[:120]), flush=True)
            continue
        print("  слов: {}, речи: {:.1f}с".format(
            len(words), sum(e - s for _w, s, e in words)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""Распознавание речи с кешем на диске.

Кеш нужен по двум причинам. Первая: повторный прогон блока не должен
заново гонять whisper по всем дублям стендапа и по всем клипам нарезки
— минуты ради уже известного ответа. Вторая, важнее: распознавание
должно быть одинаковым между шагами, иначе нарезка подбирается по
одному тексту, а в рыбу попадает другой.

Ключ кеша — модель и подпись исходника (размер и время файла). Смена
модели или подмена файла кеш обесценивают: иначе старые слова остались
бы под новым именем, и монтажёр увидел бы чужой результат.

Модель грузится один раз на процесс: она тяжёлая, а зовётся на
каждом файле.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SRC / "config") not in sys.path:
    sys.path.insert(0, str(SRC / "config"))

import bootstrap            # noqa: E402,F401
import interview            # noqa: E402


_MODELS = {}


def get_model(model_name: str, device: str = "auto"):
    """Whisper один раз на процесс.

    `speak_chunks` зовётся на каждом дубле стендапа и на каждом спикере:
    если грузить модель каждый раз, блок проводит минуты в загрузке весов
    между файлами.
    """
    key = (model_name, device)
    if key not in _MODELS:
        _MODELS[key] = interview.make_model(model_name, device)
    return _MODELS[key]


def src_sig(video: Path):
    """Подпись исходника: размер и время. Замена файла ломает кеш."""
    st = video.stat()
    return [int(st.st_size), int(st.st_mtime)]


def load_words(cache: Path, video: Path, model_name: str):
    """Ранее распознанные слова, либо None, если кеша нет или он чужой."""
    if not cache.is_file():
        return None
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("model") != model_name:
        return None
    if data.get("src") != src_sig(video):
        return None
    if not isinstance(data.get("words"), list):
        return None
    return [interview.Word(w, s, e, p) for w, s, e, p in data["words"]]


def save_words(cache: Path, video: Path, model_name: str, words) -> None:
    """Положить кеш.

    Пишем через временный файл: обрыв посреди записи оставляет
    полукорректный JSON, а он тогда просто не прочитается и файл будет
    распознан заново — результат не портится.
    """
    payload = {"model": model_name, "src": src_sig(video),
               "words": [[w.w, w.start, w.end, w.prob] for w in words]}
    tmp = cache.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(cache)
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass


def words_of(video: Path, work: Path, model_name: str, device: str = "auto",
             tag: str = ""):
    """Слова в файле: [(слово, начало, конец)].

    Пустой список означает «речи нет» — это тоже результат, и кеш его
    запоминает: клип с тишиной не гоняют через whisper второй раз.
    """
    cache = work / (video.stem + ".tr.json")
    words = load_words(cache, video, model_name)
    if words is None:
        wav = work / (video.stem + "_16k.wav")
        if not wav.exists():
            interview.extract_wav(video, wav)
        model, dev = get_model(model_name, device)
        print("  {}: распознаю...".format(video.name))
        words, _ = interview.transcribe_audio_array(
            interview.load_mono16(wav), model, tag or video.stem)
        save_words(cache, video, model_name, words)
    return [(w.w, float(w.start), float(w.end)) for w in words]

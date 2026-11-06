# -*- coding: utf-8 -*-
"""Подбор звука с рекордера к куску с камеры.

**Зачем.** В материале микрофон стоял на рекордере, а не на камере.
Поэтому у части камер (тех, что снимали в финале) своего звука почти
нет, и монтажёр кладёт голос отдельной дорожкой — с рекордера. Так
сделано в эталонном проекте: картинка с C0108, а звук с REC00016.

**Как искать.** По словам, а не по огибающей звука. Огибающая на шуме
зала совпадает плохо: замер на стендапе дал сдвиг 28.5 с против
ожидаемых 31.6 с при качестве 0.15. А слова — те же самые, и они
лежат в обоих файлах.

**Что возвращаем.** Сдвиг `rec_t = cam_t + offset` и имя файла
рекордера. По нему режется кусок звука и кладётся на отдельную
дорожку.
"""
from __future__ import annotations

import re
import unicodedata

# Слова короче трёх букв («в», «и», «не») в сопоставлении не участвуют:
# в разных записях они распознаются по-разному, а смысла не несут.
MIN_LEN = 3
# Сколько слов подряд проверяем, чтобы отсеять случайные совпадения.
CHECK = 8
# Ниже этого качества совпадение считается случайным.
MIN_QUALITY = 0.25


def norm(word: str) -> str:
    """Слово к сравнению: нижний регистр, без знаков и мягких знаков."""
    w = unicodedata.normalize("NFKD", str(word)).lower()
    w = re.sub(r"[^0-9a-zа-яё]", "", w)
    w = w.replace("ё", "е")
    return w


def key_words(words, skip: int = 0, count: int = CHECK):
    """Нормированные слова, пропустив первые `skip`."""
    out = []
    for w, s, e in words[skip:]:
        n = norm(w)
        if len(n) >= MIN_LEN:
            out.append((n, (s + e) / 2.0))
        if len(out) >= count:
            break
    return out


def _score(cam, rec, i):
    """Сколько слов из окна камеры совпало с окном рекордера.

    Совпадения идут по порядку: одно слово рекордера не может быть
    засчитано двум словам камеры. Иначе на повторяющемся слове
    («это», «вот») качество завышается, и случайный кусок проходит
    порог.
    """
    hit = 0
    j = max(0, i)
    for cw, _t in cam:
        for k in range(j, min(j + 4, len(rec))):
            if rec[k][0] == cw:
                hit += 1
                j = k + 1
                break
    return hit


def match(cam_words, rec_words):
    """Сдвиг рекордера относительно камеры (сек) и качество.

    `cam_words` — [(слово, начало, конец)] из файла камеры,
    `rec_words` — то же из файла рекордера. Возвращает
    (offset, quality) или (None, 0.0): offset такой, что
    время_рекордера = время_камеры + offset.
    """
    cam = key_words(cam_words, count=2 * CHECK)
    rec = key_words(rec_words, count=len(rec_words))
    if len(cam) < 3 or len(rec) < 3:
        return None, 0.0
    # Ищем только по местам, где стоит первое слово камеры: иначе
    # перебор всех позиций рекордера слишком долгий, а первое слово
    # почти всегда есть и там же.
    first = cam[0][0]
    places = [i for i, (rw, _t) in enumerate(rec) if rw == first]
    if not places:
        return None, 0.0
    best = None
    for i in places:
        sc = _score(cam[:CHECK], rec, i)
        if best is None or sc > best[0]:
            best = (sc, i)
    if best is None:
        return None, 0.0
    sc, i = best
    if sc < 3:
        return None, 0.0
    # Проверяем следующее окно: случайное совпадение длинного куска
    # («шесть научных фронтов») не должно проходить. Окна берём из уже
    # отфильтрованных списков: `i` — индекс в `rec`, а не в сырых
    # словах, поэтому key_words(skip=i) смещал бы окно.
    cam2 = cam[CHECK:2 * CHECK]
    rec2 = rec[i + CHECK:i + 2 * CHECK]
    if cam2 and rec2:
        sc += _score(cam2, rec2, 0)
    quality = sc / float(len(cam))
    offset = rec[i][1] - cam[0][1]
    return offset, quality


def find(cam_words, rec_index, min_quality: float = MIN_QUALITY):
    """Лучший файл рекордера и сдвиг для куска камеры.

    `rec_index` — {имя файла: [(слово, начало, конец)]}. Возвращает
    (имя файла, offset, quality) или (None, None, 0.0).
    """
    best = (None, None, 0.0)
    for name, words in rec_index.items():
        off, q = match(cam_words, words)
        if off is None:
            continue
        if q > best[2]:
            best = (name, off, q)
    if best[2] < min_quality:
        return None, None, 0.0
    return best

# -*- coding: utf-8 -*-
"""Тема нарезки — из её собственного звука.

Замер на живом материале медиацентра (126 файлов, дайджест «Выходи
решать»): в микрофон нарезки попало то, что говорили на мероприятии.
Это и есть тема кадра: «Искусственный интеллект не врет», «университет
радушно открывает дверь», «Доцент Ольга Борисовна Мизягина».

**Почему не картинка.** CLIP (openai/clip-vit-base-patch32) даёт сходство
0.58-0.74 между ЛЮБЫМИ двумя кадрами нарезки: материал с одного
мероприятия, и для модели все кадры почти одинаковы. Блоки, которые
выбрал редактор, визуально не кучны (внутри блока +0.005...+0.072 к
фону, два блока отрицательные). Значит выбор редактора смысловой, а не
художественный, и он виден в звуке, а не в картинке.

**Почему не «реплика интервью ↔ кадр» напрямую.** Общие слова у них
почти всегда пустые: нарезка — это шум зала и обрывки чужих фраз. По
IDF-совпадению выбор редактора оказывался на 84-м месте из 110, а
случайный выбор даёт 55-е. Как ЧЕРНОВИК («вот кадры про то же») —
приемлемо; как решение — нет. Решение остаётся за монтажёром, и весь
смысл модуля в том, чтобы дать ему отсортированную полку.

**Стоимость.** ASR каждого клипа нарезки — 22 минуты на 110 клипов
(large-v3, GTX 1660). Кеш в `_work`, повторный прогон — секунды.
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SRC / "config") not in sys.path:
    sys.path.insert(0, str(SRC / "config"))

import bootstrap            # noqa: E402,F401
import speech               # noqa: E402


# Слова без смысловой нагрузки. Русский текст распознаётся с мусором,
# и мусорные слова встречаются в каждом втором клипе — их вес должен
# быть нулевым, иначе сходство измеряет не тему, а шум.
STOP = set("""
и в на с по что не как это так а то он она они мы вы но или же у о из за
был были былъ к от для под при над про я ты мне его её её им нас вас свой
своя свои весь вся все всё каждый каждая каждое ещё ещё раз два три
там тут где когда который которая которые ко об до из за по над под при
про без через около после перед между среди это этого этого этом этот
эта эти его их вам нам вам мной тобой себя себя же даже только лишь
ну вот да ну уж бы же ли вот как какой какая какие там тут тогда
""".split())

_SUFFIXES = ("ами", "ями", "ыми", "ими", "ов", "ев", "ей", "ой", "ий", "ый",
             "ая", "яя", "ое", "ее", "ые", "ие", "ах", "ях", "ам", "ям",
             "ом", "ем", "ую", "юю", "ешь", "ишь", "ла", "ло", "ли", "ет",
             "ут", "ют", "ат", "ят", "им", "ыл", "ть", "ии", "ей")
# Одиночные гласные снимаем в последнюю очередь: без них «математика» и
# «математике» не сойдутся в одно слово, а реплика интервью и текст
# клипа говорят о предметах разными падежами.
_SINGLE = ("а", "я", "у", "ю", "ы", "и", "о", "е")


def stem(word: str) -> str:
    """Огрубление окончания. Не морфология, а срез до общей основы.

    Нужно, чтобы «математика», «математике» и «математику» сошлись в
    одно слово: реплика интервью и текст клипа говорят о предметах
    разными падежами, и без среза совпадений не будет вовсе.
    """
    w = word.lower().replace("ё", "е")
    for suf in _SUFFIXES:
        if len(w) - len(suf) >= 4 and w.endswith(suf):
            return w[:-len(suf)]
    for suf in _SINGLE:
        if len(w) - 1 >= 4 and w.endswith(suf):
            return w[:-1]
    return w


def content_words(text: str):
    """Значимые слова текста как основы."""
    out = set()
    for w in re.findall(r"[а-яёa-z]{4,}", (text or "").lower()):
        w = w.replace("ё", "е")
        if w in STOP:
            continue
        out.add(stem(w))
    return out


def label_of(video: Path, work: Path, model_name: str, device: str = "auto"):
    """Подпись клипа: что в его собственном звуке.

    Возвращает {"text": str, "words": [(слово, начало, конец)]}.
    """
    cache = work / (video.stem + ".clip.json")
    words = speech.words_of(video, work, model_name, device)
    text = " ".join(w for w, _s, _e in words)
    try:
        cache.write_text(json.dumps({"text": text, "model": model_name},
                                    ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return {"text": text, "words": words}


def label_pool(folder: Path, work: Path, model_name: str, device: str = "auto",
               log=None, descs=None):
    """Подписи всех клипов папки: {имя файла: подпись}.

    Подпись клипа — это то, что в нём есть: что говорит его собственный
    звук (с мероприятия) и что видно в кадре (описание моделью).

    Кеш кладём в подпапку `broll`: имена файлов у нарезки и у интервью
    пересекаются (C0098 есть и там, и там), а подпись и разметка речи
    разные вещи. Без подпапки один кеш перетирал бы другой.
    """
    cache_dir = work / "broll"
    cache_dir.mkdir(parents=True, exist_ok=True)
    descs = descs or {}
    labels = {}
    files = sorted(p for p in folder.iterdir()
                   if p.suffix.lower() in (".mp4", ".mov", ".mxf", ".avi"))
    for i, p in enumerate(files, 1):
        try:
            words = speech.words_of(p, cache_dir, model_name, device)
            text = " ".join(w for w, _s, _e in words)
        except Exception as e:
            if log:
                log("  {}: не разобрать звук ({})".format(p.name, str(e)[:70]))
            words, text = [], ""
        labels[p.name] = {"text": text, "words": words,
                           "desc": descs.get(p.name, "")}
        if log and i % 20 == 0:
            log("  {}: подписано {}/{}".format(p.name, i, len(files)))
    return labels


def label_text(lab):
    """Текст клипа целиком: что говорят и что видно."""
    parts = []
    if lab.get("text"):
        parts.append(lab["text"])
    if lab.get("desc"):
        parts.append(lab["desc"])
    return " ".join(parts).strip()


def idf(labels):
    """Вес слова по редкоте: чем реже слово в нарезке, тем весомее.

    Без этого «университет» из реплики совпадёт с половиной клипов, а
    «информатика» — с тремя, и сходство измеряет не тему, а частоту.
    """
    n = max(1, len(labels))
    df = {}
    for lab in labels.values():
        for w in content_words(label_text(lab)):
            df[w] = df.get(w, 0) + 1
    return {w: math.log(n / c) for w, c in df.items()}


def score_text(sentence: str, text: str, weights) -> float:
    """Насколько текст клипа подходит под реплику.

    Считаем по IDF-весам общих слов. Ноль означает «общих слов нет»,
    и это честный ответ: нарезка про шум зала под фразу о математике
    не подходит.
    """
    q = content_words(sentence)
    if not q:
        return 0.0
    t = content_words(text)
    if not t:
        return 0.0
    s = sum(weights.get(w, 1.0) for w in (q & t))
    return s / sum(weights.get(w, 1.0) for w in q)


# --- нейросетевое сопоставление ---
#
# Лексика по IDF не находит «математика → кадр с опытами»: общего
# слова нет. Описание кадра и реплика — русские тексты, и их сравнивает
# многоязычный кодировщик. Замер на живом материале: e5-large ставит
# выбор редактора на 24-е место из 110, тогда как лексика — на 84-е, а
# CLIP по картинке — на 45-е.
EMBED_MODEL = "intfloat/multilingual-e5-large"
_EMBEDDER = {}


def get_embedder(model_name: str = EMBED_MODEL):
    """Кодировщик один раз на прогон.

    Если видеопамять занята (например, не освободили whisper), считаем
    на процессоре: текстов мало, строк коротких, и на процессоре это
    минута, а падение с «out of memory» посреди сборки — хуже.
    """
    if model_name not in _EMBEDDER:
        from sentence_transformers import SentenceTransformer
        import torch
        dev = "cpu"
        if torch.cuda.is_available():
            free = torch.cuda.mem_get_info()[0] / (1024 ** 3)
            if free > 4.0:
                dev = "cuda"
        _EMBEDDER[model_name] = SentenceTransformer(model_name, device=dev)
    return _EMBEDDER[model_name]


def embed(texts, model_name: str = EMBED_MODEL):
    """Векторы текстов, нормированные для скалярного произведения."""
    if not texts:
        return []
    import torch
    m = get_embedder(model_name)
    with torch.no_grad():
        v = m.encode(["passage: " + t for t in texts], convert_to_numpy=True,
                     normalize_embeddings=True, show_progress_bar=False)
    return v


def embed_query(text: str, model_name: str = EMBED_MODEL):
    m = get_embedder(model_name)
    import torch
    with torch.no_grad():
        v = m.encode(["query: " + text], convert_to_numpy=True,
                     normalize_embeddings=True, show_progress_bar=False)
    return v[0]


def release_embedder():
    """Отпустить видеопамять: описание кадров и векторы — по очереди."""
    import torch
    _EMBEDDER.clear()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def embed_labels(labels, model_name: str = EMBED_MODEL):
    """Векторы подписей: {имя клипа: вектор}.

    Считаются один раз на прогон. Если считать на каждый подбор заново,
    сотня клипов умножается на число подборов — это минуты на каждый
    вызов вместо одного раза.
    """
    names = list(labels)
    if not names:
        return {}
    vecs = embed([label_text(labels[n]) for n in names], model_name)
    return dict(zip(names, vecs))


def best_matches(sentence: str, labels, weights, exclude=(), limit: int = 3,
                 neural: bool = False, vecs=None):
    """Клипы, подходящие под реплику: [(имя, оценка)], лучшие первыми.

    `exclude` — уже использованные клипы: повторять один и тот же кадр
    в ролике нельзя, монтажёр сразу это видит.
    """
    if neural and vecs:
        import numpy as np
        names = [n for n in vecs if n not in exclude]
        if names:
            q = embed_query(sentence)
            sims = np.array([float(vecs[n] @ q) for n in names])
            order = np.argsort(-sims)
            return [(names[int(i)], float(sims[int(i)])) for i in order[:limit]]
    scored = []
    for name, lab in labels.items():
        if name in exclude:
            continue
        v = score_text(sentence, label_text(lab), weights)
        if v > 0:
            scored.append((name, v))
    scored.sort(key=lambda kv: -kv[1])
    return scored[:limit]


def silent_labels(labels):
    """Клипы без речи: тему им дать нечем.

    Их нельзя подбирать под реплику, но можно ставить в паузы: там
    смысл не нужен, нужен ритм.
    """
    return [n for n, lab in labels.items() if not content_words(lab.get("text", ""))]

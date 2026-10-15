# -*- coding: utf-8 -*-
"""Смысловая связность: из набора кусков собирается цепочка.

Задача модуля. Пояснить, почему кусок B идёт после куска A, причём
не «красиво», а «по делу». Сейчас монтаж объясняет только одно: какой
план кадра не повторит предыдущий. Это вкус редактора, и он ничего не
говорит о содержании. Модуль добавляет вторую половину - связь между
кусками по смыслу, разбивку на темы и роль каждого куска в цепочке.

Чего модуль не делает и почему это правильно. Он не «понимает» текст в
человеческом смысле. Разбирать смысл до уровня, где работает LLM, без
модели нельзя, а модель в проекте пока не подключали. Зато всё, что
здесь есть, считается на голом Python: работает офлайн, мгновенно и
предсказуемо. Пропуски у него честные - смотри link_score: если
связность не нашлась, модуль так и говорит, а не делает вид, что
нашёл.

Что тут считается и почему именно это.

СВЯЗНОСТЬ. Три независимых сигнала, потому что по одному врёт:
  1. общие значимые слова (взвешенные по IDF) - куски про одно и то же;
  2. связки во втором куске («то есть», «итак», «именно») - он ссылается
     на предыдущий, а не начинает мысль с нуля;
  3. безответные указания («это», «он», «там») - во втором куске нет
     предмета, значит он предполагает, что предмет уже назван.
Если ни один не сработал - возвращаем 0 и говорим почему.

ТЕМЫ. Классический TextTiling: окна текста сравниваются между собой,
на дне сходства стоит граница темы. Без эмбеддингов - на словах, с
весами IDF. Границы нужны, чтобы не переставлять куски внутри темы и
не рвать тему пополам.

РОЛИ. Кусок не «просто хороший», а нужен именно как крючок или как
вывод. Роль назначается по маркерам: вопрос и цифра тянут в начало,
«но», «однако» - в середину, «итог», «подводя черту» - в конец.
Словари лежат в settings.json, потому что русский язык живой, и их
приходится дополнять руками.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

import settings

# ------------------------------------------------------------- словари

# Слова, которые встречаются везде и о содержании не говорят. Список
# небольшой и намеренно без «сильных» слов типа «очень»: «очень» иногда
# смыслово нагружено, и выбрасывать его автоматом нельзя.
STOPWORDS = frozenset("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы
по только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг
ли если уже или ни быть был него до вас нибудь опять уж вам сказал ведь там
потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была
сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому
этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были
куда зачем всех никогда можно при наконец два об другой хоть после над больше
тот через эти нас про всего них какая много разве три эту моя впрочем хорошо
свою этой перед иногда лучше чуть том нельзя такой им более всегда конечно
всю между
""".split())

# Окончания для огрубления русских слов. Полноценного стеммера нет и
# не нужно: нам важно, чтобы «куски» и «кусок» сошлись, а не чтобы
# получился правильный корень. Отрезаем только если остаток достаточно
# длинный, иначе короткие слова съедают сами себя.
_ENDINGS = (
    "иями", "ями", "ами", "ыми", "ими", "ах", "ях", "ой", "ей", "ый",
    "ий", "ая", "яя", "ое", "ее", "ые", "ие", "ам", "ям", "ом", "ем",
    "ух", "их", "ов", "ев", "ью", "ия", "ие", "ть", "ся", "сь",
    "ла", "ло", "ли", "ет", "ит", "ут", "ют", "ат", "ят", "ал",
    "ать", "ять", "ует", "ает", "ется", "ются", "ился", "илась",
    # одно-буквенные идут последними, чтобы длинные сработали первыми.
    # Без них «аренда» и «аренду» остаются разными словами, и по всей
    # разметке сходство нулевое - темы не находятся вообще.
    "а", "я", "у", "ю", "ы", "и", "о", "е", "ь",
)

# Связки, которые прямо ссылаются на предыдущее. Сильный сигнал
# продолжения - но только если стоят в начале куска.
CONTINUATIVES = (
    "то есть", "иными словами", "другими словами", "итак", "так вот",
    "именно", "поэтому", "потому что", "следовательно", "по этой причине",
    "как я говорил", "я говорил", "как мы обсуждали", "в общем",
    "вот что", "получается", "в общем так", "подводя итог", "если так",
    "и это значит", "а значит", "соответственно", "кстати", "более того",
    "на самом деле", "по большому счету",
)

# Указания без названного предмета. Если во втором куске «это» и ни одного
# существительного - он рассчитывает, что предмет уже был назван.
ANAPHORA = frozenset("""
это этот этою таких такая такие такого тем тому этим эта эти он она оно
они там тут тогда здесь оттуда сюда таким такой так таков этом этому этом
""".split())

# Роли. Ключ - название роли, значение - веса и маркеры.
ROLE_HINT = {
    "крючок": {
        "markers": ("?", "сколько", "почему", "зачем", "как же", "а вы",
                    "представьте", "попробуйте", "угадайте", "не поверите"),
        "w": 1.0,
    },
    "контекст": {
        "markers": ("имеется в виду", "иными словами", "то есть",
                    "это значит", "для начала", "итак речь", "контекст",
                    "прежде всего", "история такая", "речь пойдет"),
        "w": 1.0,
    },
    "тезис": {
        "markers": ("я считаю", "по-моему", "главное", "суть в том",
                    "проблема в том", "дело в том", "важно понимать",
                    "нужно понимать", "основная мысль", "вывод"),
        "w": 1.0,
    },
    "пример": {
        "markers": ("например", "вот пример", "взять к примеру", "случай",
                    "конкретно", "цифра", "процентов", "процент",
                    "миллионов", "тысяч человек", "вот что получилось"),
        "w": 1.0,
    },
    "возражение": {
        "markers": ("но есть", "однако", "с другой стороны", "не согласен",
                    "возражение", "возразить", "спорный момент",
                    "но я не соглашусь", "в этом я не уверен"),
        "w": 1.0,
    },
    "вывод": {
        "markers": ("подводя итог", "итог", "резюме", "так что", "в общем",
                    "подводя черту", "если коротко", "значит, итог",
                    "напоследок", "заканчивая"),
        "w": 1.0,
    },
}

# Роли, которыми цепочка открывается и закрывается.
ROLE_OPEN = ("крючок",)
ROLE_CLOSE = ("вывод",)


def strong_words() -> Dict[str, List[str]]:
    """Словари «сильных» слов берём из настроек новостного сюжета: там
    они уже есть, и держать второй такой же список значит завести два
    места, где их придётся править."""
    return settings.get("news.strong_words", {}) or {}


# ------------------------------------------------------------- текст

_WORD_RX = re.compile(r"[a-zA-Zа-яА-ЯёЁ][a-zA-Zа-яА-ЯёЁ0-9\-]*")


def stem(word: str) -> str:
    """Огрубление окончания. Не стеммер, а «достаточно для совпадения»."""
    w = word.lower().replace("ё", "е")
    if len(w) <= 4:
        return w
    for e in _ENDINGS:
        if w.endswith(e) and len(w) - len(e) >= 4:
            return w[:-len(e)]
    return w


def tokenize(text: str) -> List[str]:
    """Значимые слова текста: без стоп-слов и коротких обрезков."""
    out = []
    for m in _WORD_RX.findall(text or ""):
        w = stem(m)
        if len(w) < 4 or w in STOPWORDS:
            continue
        out.append(w)
    return out


class Index:
    """Веса слов по всему материалу (IDF).

    Слова, которые встретились в половине кусков, ничего не различают -
    они и не должны влиять на решение. Зато редкие различают сильно,
    и именно они связывают куски по теме.
    """

    def __init__(self, docs: Sequence[str]):
        self.n = max(1, len(docs))
        df: Dict[str, int] = {}
        self.docs_tokens: List[List[str]] = []
        for d in docs:
            toks = tokenize(d)
            self.docs_tokens.append(toks)
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        self.idf: Dict[str, float] = {
            t: math.log(1.0 + self.n / (1.0 + c)) for t, c in df.items()}

    def weight(self, token: str) -> float:
        return self.idf.get(token, 0.0)

    def vector(self, tokens: Sequence[str]) -> Dict[str, float]:
        """Нормированный вектор весов: единичная длина, чтобы косинус
        считался обычным скалярным произведением."""
        v: Dict[str, float] = {}
        for t in set(tokens):
            w = self.idf.get(t)
            if w and w > 0:
                v[t] = w
        norm = math.sqrt(sum(x * x for x in v.values()))
        if norm <= 0:
            return {}
        return {t: x / norm for t, x in v.items()}

    def of(self, text: str) -> Dict[str, float]:
        return self.vector(tokenize(text))


def cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(t, 0.0) for t, w in a.items())


# ------------------------------------------------------------- связность

@dataclass
class Link:
    score: float                      # 0..1
    lex: float = 0.0
    marker: float = 0.0
    anaphora: float = 0.0
    reason: str = ""

    @property
    def joined(self) -> str:
        """Связка ради связки, но всё же разных кусков."""
        return self.reason


def has_continuative(text: str, head_chars: int = 45) -> bool:
    """Начинается ли кусок со связки, ссылающейся на предыдущее.

    Смотрим только самое начало, а не первые полторы строки. Иначе
    «кстати», сказанное в середине фразы, считалось бы началом
    продолжения, и монтаж склеивал бы куски на пустом месте. На живом
    интервью это давало ложную склейку: кусок начинался с «я хотел бы у
    тебя спросить», а «кстати» стояло через сто символов.
    """
    low = " " + (text or "").lower().replace("ё", "e").strip() + " "
    head = low[:head_chars + 2]
    return any(m in head for m in CONTINUATIVES)


def anaphora_share(text: str) -> float:
    """Доля указаний без предмета.

    Слова сверяются СЫРЫМИ, а не огрублёнными, и это не случайно.
    Огрубление ломает сами указания: «такая» превращается в «така»,
    «тогда» в «тогд», «здесь» в «здес». Из двадцати пяти слов в списке
    семь так и не могли сработать никогда, и самый частый случай «этом»
    в списке не было вовсе.
    """
    words = [(m or "").lower().replace("ё", "е") for m in _WORD_RX.findall(text or "")]
    if not words:
        return 0.0
    hits = sum(1 for w in words if w in ANAPHORA)
    return hits / len(words)


def link_score(a_text: str, b_text: str, idx: Index) -> Link:
    """Насколько кусок B продолжает кусок A. 0..1.

    Три сигнала складываются с весами из настроек, но не на единицу:
    каждый по отдельности слабый, и три слабых сигнала вместе дают
    уверенность там, где один дал бы уверенность на пустом месте.
    """
    lex = cosine(idx.of(a_text), idx.of(b_text))
    marker = 1.0 if has_continuative(b_text) else 0.0
    ana = min(1.0, anaphora_share(b_text) * 6.0)

    w_lex = float(settings.get("narrative.w_lexical", 1.0))
    w_mark = float(settings.get("narrative.w_marker", 0.35))
    w_ana = float(settings.get("narrative.w_anaphora", 0.20))
    # Пересечение по теме занимает шкалу само по себе, поэтому w_lexical
    # это МАСШТАБ, а не доля: при 0.5 пересечение даёт половину шкалы,
    # при 2.0 переполняет её и упирается в единицу. Складывать все три
    # веса было нельзя: деление на их сумму означало, что идеальное
    # совпадение по теме даёт 0,59 вместо единицы, и любой порог
    # в настройках вёл себя неожиданно.
    lex = min(1.0, w_lex * lex)
    score = max(0.0, min(1.0, lex + w_mark * marker + w_ana * ana))

    parts = []
    if lex > 0.12:
        parts.append(f"общие темы {lex:.2f}")
    if marker:
        parts.append("связка во втором куске")
    if ana > 0.3:
        parts.append(f"указания без предмета {ana:.2f}")
    reason = "; ".join(parts) if parts else "смысловой связи не найдено"
    return Link(score, lex, marker, ana, reason)


# ------------------------------------------------------------- темы

def enabled() -> bool:
    """Включено ли повествование. Выключатель нужен не для красоты:
    без него собранный ранее монтаж нельзя пересобрать «как было», не
    отключив новую логику в коде."""
    return bool(settings.get("narrative.enabled", True))


def topic_window() -> int:
    return max(1, int(settings.get("narrative.topic_window", 3)))


def segment_topics(fragments: Sequence[Dict], idx: Index,
                   window: int = 0,
                   min_topic: int = 2) -> List[int]:
    """Номер темы для каждого куска. Куски идут по времени.

    TextTiling. Сначала считаем сходство между окнами слева и справа от
    каждой позиции: пока тема одна, сходство высокое. На границе темы
    оно проваливается. Глубина провала считается не от нуля, а от
    ближайших пиков - иначе граница не отличилась бы от простого
    перехода, где сходство низкое, но и падать было нечему.

    Подтверждаем границу, только если провал глубокий: одиночная пауза
    или короткая реплика не должны разрезать тему надвое.
    """
    n = len(fragments)
    if n == 0:
        return []
    # Окно подстраивается под длину материала. При окне в три куска на
    # семи кусках текста внутренних позиций остаётся одна, и границу
    # искать негде: на коротких кусках темы не находятся вообще.
    window = max(1, min(int(window) or topic_window(), max(1, n // 4)))
    if n <= 2 * window or n <= min_topic:
        return [0] * n

    vecs = [idx.of(f.get("text") or "") for f in fragments]
    lo, hi = window, n - window

    sim = [0.0] * n
    for i in range(lo, hi):
        sim[i] = cosine(_pool(vecs, i - window, i), _pool(vecs, i, i + window))

    depth = [0.0] * n
    for i in range(lo, hi):
        left_slice = sim[max(0, i - window):i]
        right_slice = sim[i + 1:min(hi, i + window + 1)]
        left_peak = max(left_slice) if left_slice else sim[i]
        right_peak = max(right_slice) if right_slice else sim[i]
        depth[i] = (max(0.0, left_peak - sim[i])
                    + max(0.0, right_peak - sim[i]))
    depth = _smooth(depth)

    thr = float(settings.get("narrative.topic_cut", 0.20))
    cuts = []
    for i in range(max(1, lo), min(n - 1, hi)):
        if depth[i] > thr and depth[i] >= depth[i - 1] and depth[i] >= depth[i + 1]:
            cuts.append(i)

    min_gap = max(1, int(settings.get("narrative.topic_min_gap", 2)))
    kept: List[int] = []
    for i in cuts:
        if kept and i - kept[-1] < min_gap:
            if depth[i] > depth[kept[-1]]:
                kept[-1] = i
            continue
        kept.append(i)

    topics = [0] * n
    t = 0
    for i in kept:
        t += 1
        for j in range(i, n):
            topics[j] = t
    return _merge_short_topics(topics, min_topic)


def _pool(vecs: Sequence[Dict[str, float]], a: int, b: int) -> Dict[str, float]:
    acc: Dict[str, float] = {}
    for v in vecs[a:b]:
        for t, w in v.items():
            acc[t] = acc.get(t, 0.0) + w
    norm = math.sqrt(sum(x * x for x in acc.values()))
    return {t: x / norm for t, x in acc.items()} if norm > 0 else {}


def _smooth(depth: Sequence[float]) -> List[float]:
    out = list(depth)
    for i in range(1, len(depth) - 1):
        out[i] = (depth[i - 1] + 2 * depth[i] + depth[i + 1]) / 4.0
    return out


def _merge_short_topics(topics: List[int], min_topic: int) -> List[int]:
    if not topics:
        return topics
    out = list(topics)
    t = 0
    i = 0
    n = len(out)
    while i < n:
        j = i
        while j + 1 < n and out[j + 1] == out[i]:
            j += 1
        length = j - i + 1
        if length < min_topic and i > 0:
            for k in range(i, j + 1):
                out[k] = out[i - 1]
        else:
            for k in range(i, j + 1):
                out[k] = t
            t += 1
        i = j + 1
    return out


# ------------------------------------------------------------- роли

@dataclass
class Role:
    role: str
    score: float
    why: str = ""


def _marker_hits(text: str, markers: Sequence[str]) -> int:
    low = " " + (text or "").lower().replace("ё", "e") + " "
    return sum(1 for m in markers if m in low)


def classify_role(text: str, idx: Optional[Index] = None) -> Role:
    """Какую роль в цепочке играет кусок.

    Порядок проверки не случаен: маркеры конца спора проверяются раньше
    маркеров начала, иначе кусок «итак, подводя итог» считался бы
    крючком из-за вопросительного знака в середине.
    """
    t = text or ""
    low = " " + t.lower().replace("ё", "e") + " "
    sw = strong_words()
    emotion = _marker_hits(low, sw.get("emotion", []))
    conflict = _marker_hits(low, sw.get("conflict", []))

    digits = len(re.findall(r"\d", t))
    question = t.count("?")
    words = tokenize(t)
    short = len(words) < 18

    scores: Dict[str, float] = {}
    why: Dict[str, str] = {}

    # --- конец: итоговые маркеры перевешивают всё остальное
    hits = _marker_hits(low, ROLE_HINT["вывод"]["markers"])
    if hits:
        scores["вывод"] = 2.0 + hits
        why["вывод"] = f"итоговые маркеры ({hits})"

    # --- спор
    hits = _marker_hits(low, ROLE_HINT["возражение"]["markers"])
    if hits:
        scores["возражение"] = 1.6 + hits + conflict * 0.8
        why["возражение"] = f"маркеры спора ({hits})"
    if re.search(r"\bно\b", low) and conflict:
        scores["возражение"] = scores.get("возражение", 0.0) + 0.6

    # --- начало: крючок
    hook = 0.0
    why_hook = []
    if question:
        hook += 1.4
        why_hook.append("вопрос")
    if digits:
        hook += 0.9
        why_hook.append("цифра")
    if emotion or conflict:
        hook += 0.8 * min(emotion + conflict, 2)
        why_hook.append("сильная эмоция" if emotion else "конфликт")
    hits = _marker_hits(low, ROLE_HINT["крючок"]["markers"])
    if hits:
        hook += 1.0 + hits * 0.5
        why_hook.append(f"маркеры крючка ({hits})")
    # Короткий кусок крючком не становится. Раньше здесь стояло +0.4
    # за длину, и из-за этого пустая реплика проходила как начало
    # ролика: других признаков нет, а длина уже «за крючок».
    # Теперь короткий текст только усиливает крючок, если тот есть.
    if hook > 0 and short:
        hook += 0.4
        why_hook.append("короткий")
    if hook > 0:
        scores["крючок"] = hook
        why["крючок"] = ", ".join(why_hook)

    # --- пример: цифра или явный маркер
    ex = _marker_hits(low, ROLE_HINT["пример"]["markers"])
    if digits or ex:
        scores["пример"] = 1.2 + 0.4 * min(digits, 3) + ex
        why["пример"] = "цифра" if digits else f"маркеры примера ({ex})"

    # --- тезис: утверждение без признаков примера
    hits = _marker_hits(low, ROLE_HINT["тезис"]["markers"])
    if hits:
        scores["тезис"] = 1.5 + hits
        why["тезис"] = f"маркеры тезиса ({hits})"
    elif scores and not question and 18 <= len(words) < 60 and not digits:
        scores["тезис"] = 1.0
        why["тезис"] = "утверждение средней длины без цифр"

    # --- контекст: объяснение
    hits = _marker_hits(low, ROLE_HINT["контекст"]["markers"])
    if hits:
        scores["контекст"] = 1.4 + hits
        why["контекст"] = f"пояснительные маркеры ({hits})"

    if not scores:
        return Role("связка", 0.5, "роль не определяется — держим в цепочке")
    role = max(scores, key=lambda r: scores[r])
    return Role(role, float(scores[role]), why.get(role, ""))
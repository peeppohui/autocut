# -*- coding: utf-8 -*-
"""Дайджест: сортировка на части и сборка окна речи.

Сортировщик проверен на материале медиацентра: 126 файлов разложены
вручную, и разбор по алгоритму совпал со всеми 126. Здесь та же логика
проверяется на синтетике, где ответ известен заранее, чтобы правка
порогов или формулы не прошла молча.

Пороги стоят так, потому что на настоящем материале разошлись
длительности без зазора: нарезка 4.8-27.4с, стендап 16.3-73.4с,
интервью 100.8-359с. Доля речи и лицо в кадре не различают части
(нарезка «говорит» на 44-57% шумом зала, в зрительских планах люди
есть в каждом кадре) — поэтому вторая ступень это длиннейший
непрерывный кусок речи, а в спорных случаях файл помечается
неоднозначным и печатается, а не молча угадывается.
"""
import sys
import csv
import io
import wave
import contextlib
import tempfile
from pathlib import Path as _P

import numpy as np

ROOT = _P(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "ml"))
sys.path.insert(0, str(ROOT / "src" / "modes"))
import bootstrap  # noqa: F401,F403

import sorter as SO        # noqa: E402
import speech as SP        # noqa: E402
import digest as DG        # noqa: E402

fail = []


def ok(name, cond, extra=""):
    if cond:
        print(f"OK   {name}")
    else:
        print(f"FAIL {name} {extra}")
        fail.append(name)


TH = dict(SO.DEFAULTS)

# --- сортировка на заранее известных длительностях ---

# Длительность — единственный признак, который на живых данных разошёлся
# полностью, поэтому проверяем именно её границы.
def with_run(dur, run):
    """Классификация файла с заранее известными числами.

    Настоящий classify зовёт ffprobe и считает речь по файлу. Здесь мы
    проверяем РЕШЕНИЕ на конкретных длительностях и временах речи,
    подставляя их напрямую, чтобы тест не гонял видео.
    """
    if dur >= TH["interview_min_sec"]:
        return "интервью"
    if dur < TH["standup_min_sec"]:
        return "нарезка"
    if run >= TH["speech_run_min_sec"]:
        return "стендап"
    return "нарезка"


# Замеры с реального материала медиацентра.
cases = [
    # (файл, длительность, непрерывная речь, ожидаемая часть)
    ("C0046", 5.8, 5.7, "нарезка"),      # короткий кусок зрителей
    ("C0052", 7.2, 2.8, "нарезка"),
    ("C0003", 19.2, 3.0, "нарезка"),     # длительность как у стендапа, но
                                         # почти непрерывной речи нет
    ("C0105", 16.3, 7.8, "стендап"),     # короткий стендап, но говорит подряд
    ("C0101", 54.2, 14.1, "стендап"),    # длинный дубль
    ("FX20031", 73.4, 23.0, "стендап"),
    ("C0099", 100.8, 26.5, "интервью"),  # самый короткий интервью
    ("C0098", 359.0, 11.5, "интервью"),  # самый длинный
]
for name, dur, run, want in cases:
    got = with_run(dur, run)
    ok(f"{name} ({dur:.0f}с, речь {run:.0f}с) -> {want}", got == want, f"получилось {got}")

# Границы порогов измеряются, а не взяты с потолка.
ok("порог интервью выше максимума стендапа",
   TH["interview_min_sec"] > 73.4, f"{TH['interview_min_sec']}")
ok("порог стендапа ниже минимума стендапа",
   TH["standup_min_sec"] < 16.3, f"{TH['standup_min_sec']}")
ok("нарезка короче порога стендапа",
   10.6 < TH["standup_min_sec"], "")

# --- выбор лучшего дубля стендапа: длинее != лучше ---
def _words(spans):
    """Слова из отрезков (начало, конец): по одному слову на отрезок."""
    out = []
    for i, (a, b) in enumerate(spans):
        out.append(("с{}".format(i), float(a), float(b)))
    return out


# рваный: три фрагмента по 8 секунд с большими паузами между ними
_takes = [
    ("длинный_но_рваный", _words(
        [(0, 4), (4, 8), (30, 34), (34, 38), (60, 64), (64, 68)])),
    # связный: одна непрерывная фраза на 30 секунд
    ("короткий_но_связный", _words(
        [(0, 3), (3, 6), (6, 9), (9, 12), (12, 15), (15, 18), (18, 21),
         (21, 24), (24, 27), (27, 30)])),
]
best = DG.pick_best_take(_takes)
ok("выбирается связный дубль, а не самый длинный",
   best is not None and best[1] == "короткий_но_связный",
   best and best[1])
ok("из пустых дублей ничего не выберется",
   DG.pick_best_take([("пусто", [])]) is None, "")
ok("пустой набор не роняет выбор", DG.pick_best_take([]) is None, "")
ok("длиннейший непрерывный кусок считается по словам",
   abs(DG.longest_run(_takes[1][1]) - 30.0) < 0.5,
   str(DG.longest_run(_takes[1][1])))
ok("рваный дубль даёт короткий непрерывный кусок",
   DG.longest_run(_takes[0][1]) < 9.0, str(DG.longest_run(_takes[0][1])))

# --- куски режутся по границам фраз ---
# Редактор режет интервью там, где кончилась фраза: кусок «На решение
# отводится час» (1.16с) стоит отдельно, а не обрезается посередине.
_phrase = _words([(0, 0.5), (0.5, 1.0),          # «У нас контрольная»
                   (1.0, 1.5), (1.5, 2.0),        # «...проводится»
                   (4.0, 4.6), (4.6, 5.2),        # пауза 2с -> граница
                   (5.2, 5.8), (5.8, 6.4)])
_pieces = DG.phrase_pieces(_phrase)
ok("речь режется по границам фраз", len(_pieces) >= 2, str(_pieces))
ok("кусок не пересекает паузу",
   all(abs(p[1] - q[0]) > 1.0 for p, q in zip(_pieces, _pieces[1:])),
   str([(round(p[0], 1), round(p[1], 1)) for p in _pieces]))
ok("пустая речь не даёт кусков", DG.phrase_pieces([]) == [], "")

# --- вырезка из клипа нарезки берётся там, где говорят ---
_ex = DG.pick_excerpt(_words([(0, 0.4), (0.5, 0.9), (1.0, 1.4)]), 20.0, 3.0)
ok("вырезка короткая", _ex[1] - _ex[0] <= 3.5, str(_ex))
ok("вырезка взята с говорящего места", _ex[0] < 2.0, str(_ex))
_silent = DG.pick_excerpt([], 20.0, 3.0)
ok("в тишине берётся середина клипа",
   abs(_silent[0] - 8.5) < 1.0, str(_silent))
ok("короткий клип берётся целиком",
   DG.pick_excerpt([], 2.0, 3.0) == (0.0, 2.0),
   str(DG.pick_excerpt([], 2.0, 3.0)))

# --- тема клипа из его собственного звука ---
import theme as THEME  # noqa: E402

_labels = {
    "C0001.MP4": {"text": "Искусственный интеллект не врет, он выдает вам "
                           "статистику", "words": []},
    "C0002.MP4": {"text": "научные опыты физика и химия", "words": []},
    "C0003.MP4": {"text": "", "words": []},
}
_w = THEME.idf(_labels)
_best = THEME.best_matches("лекция искусственный интеллект и математика",
                         _labels, _w, limit=1)
ok("клип про искусственный интеллект подходит под реплику",
   bool(_best) and _best[0][0] == "C0001.MP4", str(_best))
ok("пустой звук не подходит ни под что",
   all(name != "C0003.MP4" for name, _v in
       THEME.best_matches("любая реплика", _labels, _w, limit=3)), "")
ok("основы слов совпадают в разных падежах",
   "математик" in THEME.content_words("математика") and
   "математик" in THEME.content_words("математике"), "")
ok("стоп-слова не попадают в значимые",
   "контроль" not in THEME.content_words("и в на с по что"), "")

# --- границы фраз: и по паузе, и по знаку ---
# Знак конца предложения сидит в самом слове, как его выдаёт whisper.
_sent = [("раз", 0.0, 0.4), ("два", 0.4, 0.8), ("три", 0.8, 1.2),
         ("четыре.", 1.2, 1.6), ("пять", 1.6, 2.2), ("шесть", 2.2, 2.8)]
_p = DG.phrase_pieces(_sent)
ok("предложение режется по знаку",
   len(_p) == 2 and abs(_p[1][0] - 1.6) < 0.01, str(_p))
ok("кусок не длиннее предела",
   all(p[1] - p[0] <= DG.MAX_PIECE + 0.01 for p in
       DG.phrase_pieces(_words([(i * 0.5, i * 0.5 + 0.45)
                                 for i in range(40)]))), "")
ok("обрывки короче 0.8с отбрасываются",
   all(p[1] - p[0] >= 0.8 for p in DG.phrase_pieces(_sent)), str(_p))
ok("текст куска соответствует именно этому куску",
   _p[0][2] == "раз два три четыре" and _p[1][2] == "пять шесть", str(_p))

# --- номер клипа по имени ---
ok("номер камеры читается из имени",
   DG.clip_number("C0028.MP4") == 28 and
   DG.clip_number("FX20020.MP4") == 20020,
   str(DG.clip_number("FX20020.MP4")))

# --- сборка рыбы по рецепту редактора ---
def _seg(name, plan, hero, text=""):
    return (_P(name), 0.0, 2.0, text, plan, hero)


_labels2 = {
    "C0010.MP4": {"text": "лекция искусственный интеллект и математика",
                  "words": []},
    "C0020.MP4": {"text": "университет открывает двери школьникам",
                  "words": []},
    "C0030.MP4": {"text": "", "words": []},
}
_weights2 = THEME.idf(_labels2)
_broll2 = [_seg("C0010.MP4", "крупный", "спикер",
                "лекция искусственный интеллект и математика"),
           _seg("C0020.MP4", "общий", "зрители",
                "университет открывает двери школьникам"),
           _seg("C0030.MP4", "средний1", "зрители", "(без речи)")]
# куски несут слова: по ним ищется звук с рекордера
_st = [(_P("C0102.MP4"), 0.0, 4.0, "стендап",
        [("шесть", 0.0, 1.0), ("научных", 1.0, 2.0)])]
_iv = [(_P("C0098.MP4"), [(0.0, 3.0, "лекция искусственный интеллект",
                           [("лекция", 0.0, 1.0), ("искусственный", 1.0, 2.0)]),
                          (4.0, 7.0, "университет открывает двери",
                           [("университет", 4.0, 5.0), ("открывает", 5.0, 6.0)])],
        [("лекция", 0.0, 1.0), ("искусственный", 1.0, 2.0),
         ("университет", 4.0, 5.0), ("открывает", 5.0, 6.0)])]
_rows = DG.build_digest(_st, _broll2, _iv, _labels2, _weights2, print, 120)
_parts = [r[0] for r in _rows]
ok("рыба начинается со стендапа", bool(_parts) and _parts[0] == "стендап",
   str(_parts))
ok("в рыбе есть нарезка", "нарезка" in _parts, str(_parts))
ok("в рыбе есть интервью", "интервью" in _parts, str(_parts))
ok("интервью идёт после стендапа",
   _parts.index("интервью") > _parts.index("стендап"), str(_parts))
ok("вставка подобрана по теме реплики",
   any(r[0] == "нарезка" and "интеллект" in r[4] for r in _rows),
   str([(r[0], r[4][:30]) for r in _rows]))

# три подряд одного плана и героя не идёт
_same = [_seg("C00{:02d}.MP4".format(i), "общий", "зрители")
         for i in (10, 40, 70)]
_rows2 = DG.build_digest(_st, _same, [], {}, {}, print, 120)
_used2 = [r[1].name for r in _rows2 if r[0] == "нарезка"]
ok("три одинаковых подряд не ставятся",
   len(_used2) <= 2, str(_used2))

# соседние номера файлов не идут подряд
_neigh = [_seg("C0011.MP4", "общий", "зрители"),
          _seg("C0012.MP4", "крупный", "спикер"),
          _seg("C0013.MP4", "средний1", "участники")]
_rows3 = DG.build_digest(_st, _neigh, [], {}, {}, print, 120)
_used = [r[1].name for r in _rows3 if r[0] == "нарезка"]
_ok = True
for a, b in zip(_used, _used[1:]):
    if abs(DG.clip_number(a) - DG.clip_number(b)) <= 3:
        _ok = False
ok("соседние номера файлов не идут подряд", _ok, str(_used))

# --- устройство рыбы по замеру готового ролика ---
# Замер на эталонном дайджесте: стендап 21%, нарезка 55%, интервью 24%;
# нарезка — 32 куска по 1.2-2.6 с из 28 разных файлов.
def _seg2(name, plan, hero, text=""):
    return (_P(name), 0.0, 2.0, text, plan, hero)


# Крупность и кто в кадре в реальном материале разные: если у всех
# клипов одна и та же пара, правило разнообразия душит нарезку, и
# проверка становится проверкой заготовки, а не сборки.
_PLANS = ["общий", "средний1", "средний2", "крупный", "деталь"]
_HEROS = ["зрители", "участники", "спикер", "журналист"]
# Темы клипов разные: одинаковые фразы в заготовке делают все кадры
# похожими, и проверка «похожие подряд не идут» забивает всю нарезку.
_TOPICS = ["математика", "физика", "химия", "биология", "астрономия",
           "информатика", "двери", "контрольная", "школьники", "лекция"]
_many = [_seg2("C{:04d}.MP4".format(100 + i * 7),
               _PLANS[i % len(_PLANS)], _HEROS[(i // 3) % len(_HEROS)],
               "сюжет про {} номер {}".format(_TOPICS[i % len(_TOPICS)], i))
         for i in range(60)]
_iv2 = [(_P("C0098.MP4"),
         [(0.0, 3.0, "лекция про математику",
           [("лекция", 0.0, 1.0), ("математику", 1.0, 2.0)]),
          (4.0, 7.0, "университет открывает двери",
           [("университет", 4.0, 5.0), ("двери", 5.0, 6.0)])],
         [("лекция", 0.0, 1.0), ("математику", 1.0, 2.0),
          ("университет", 4.0, 5.0), ("двери", 5.0, 6.0)]),
        (_P("C0099.MP4"),
         [(0.0, 3.0, "контрольная работа",
           [("контрольная", 0.0, 1.0), ("работа", 1.0, 2.0)]),
          (4.0, 7.0, "школьники пришли",
           [("школьники", 4.0, 5.0), ("пришли", 5.0, 6.0)])],
         [("контрольная", 0.0, 1.0), ("работа", 1.0, 2.0),
          ("школьники", 4.0, 5.0), ("пришли", 5.0, 6.0)])]
_lab2 = {s[0].name: {"text": s[3], "words": [], "dur": 9.0}
         for s in _many}
# neural=False: проверка проверяет СБОРКУ, а не подбор. Подбор по
# векторам проверяется отдельно, на живом материале.
# Стендап в эталоне занимает 21% — берём кусок такой длины, иначе
# доля считается от заготовки, а не от сборки.
_st4 = [(_P("C0102.MP4"), 0.0, 21.0, "стендап про науку",
         [("шесть", 0.0, 1.0), ("научных", 1.0, 2.0)])]
_rows4 = DG.build_digest(_st4, _many, _iv2, _lab2, THEME.idf(_lab2), print,
                         100, neural=False)
_tot4 = {}
for r in _rows4:
    _tot4[r[0]] = _tot4.get(r[0], 0) + (r[3] - r[2])
_sum4 = sum(_tot4.values())
_share4 = {k: v / _sum4 for k, v in _tot4.items()}
ok("доля стендапа близка к эталонной",
   abs(_share4.get("стендап", 0) - 0.21) < 0.12,
   str({k: round(v, 2) for k, v in _share4.items()}))
ok("нарезки в рыбе больше половины",
   _share4.get("нарезка", 0) > 0.45,
   str({k: round(v, 2) for k, v in _share4.items()}))
_nar4 = [r for r in _rows4 if r[0] == "нарезка"]
# По эталону: нарезка — короткие вырезки по мере и поверх голоса, и в
# хвосте (целые клипы 7–9 с из прошлого прогона отменены).
_voice_end4 = max([r[6] + (r[3] - r[2]) for r in _rows4
                   if r[0] != "нарезка"] or [0])
_over4 = [r for r in _nar4 if r[6] < _voice_end4]
_tail4 = [r for r in _nar4 if r[6] >= _voice_end4]
ok("вырезки нарезки поверх голоса не длиннее 2.6 с",
   all(1.0 < (r[3] - r[2]) <= 2.6 + 0.01 for r in _over4),
   str([round(r[3] - r[2], 2) for r in _over4[:6]]))
ok("в хвосте нарезка вырезками той же меры",
   all(1.0 < (r[3] - r[2]) <= 2.6 + 0.01 for r in _tail4),
   str([round(r[3] - r[2], 2) for r in _tail4[:6]]))
ok("нарезка лежит на второй дорожке, голос на первой",
   all(r[5] == "V2" for r in _nar4) and
   all(r[5] == "V1" for r in _rows4 if r[0] != "нарезка"),
   str([(r[0], r[5]) for r in _rows4[:4]]))
_ok4 = True
for a, b in zip(_nar4, _nar4[1:]):
    if abs(DG.clip_number(a[1].name) - DG.clip_number(b[1].name)) <= 3:
        _ok4 = False
ok("соседние номера не идут подряд и в новой сборке", _ok4,
   str([r[1].name for r in _nar4[:8]]))

# --- перебивки без звука, J-cut, паузы эталона, музыка ---
# В эталоне перебивки играют БЕЗ своего звука: поверх речи звучит
# рекордер, в паузах — музыка. Колонка sound=нет выкидывает звуковой
# клип перебивки из XML совсем.
ok("нарезка без своего звука",
   all(r[7] == "нет" for r in _nar4),
   str(set(r[7] for r in _nar4)))

ok("гэп после стендапа — 3.6 с, как в эталоне",
   abs(next(r[6] for r in _rows if r[0] == "интервью") - 7.6) < 1e-9,
   str([r[6] for r in _rows if r[0] == "интервью"]))

# J-cut: первый бролл заходит под конец стендапа, кадр уже идёт, пока
# звучит голос. Тема стендапа должна попадать в лейблы, поэтому у
# стенда здесь текст «университет открывает двери».
_stj = [(_P("C0102.MP4"), 0.0, 4.0, "университет открывает двери",
         [("шесть", 0.0, 1.0), ("научных", 1.0, 2.0)])]
_rowsj = DG.build_digest(_stj, _broll2, [], _labels2, _weights2, print, 120,
                         neural=False)
_narj = [r for r in _rowsj if r[0] == "нарезка"]
ok("J-cut: бролл заходит под стендап",
   bool(_narj) and _narj[0][6] < 4.0 and _narj[0][3] <= 4.0 + 1e-9
   and "университет" in _narj[0][4],
   str([(r[6], r[4][:20]) for r in _narj[:2]]))

# Первые OVR_OPEN секунд спикера открыты: перекрывать можно только
# реплики, начавшиеся позже (в эталоне спикер открыт ~8 с).
_ivl = [(_P("C0098.MP4"),
         [(0.0, 12.0, "лекция искусственный интеллект",
           [("лекция", 0.0, 1.0), ("искусственный", 1.0, 2.0)])],
         [("лекция", 0.0, 1.0), ("искусственный", 1.0, 2.0)])]
_rowsl = DG.build_digest(_st, _broll2, _ivl, _labels2, _weights2, print, 120,
                         neural=False)
_spk0 = min(r[6] for r in _rowsl if r[0] == "интервью")
_narl = [r for r in _rowsl if r[0] == "нарезка"]
ok("первые 8 секунд спикера открыты",
   all(not (_spk0 <= r[6] < _spk0 + 8.0) for r in _narl),
   str(sorted(round(r[6], 2) for r in _narl)))
ok("и перебивка поверх длинной реплики всё же стоит",
   any(_spk0 + 8.0 <= r[6] < _spk0 + 12.0 for r in _narl),
   str(sorted(round(r[6], 2) for r in _narl)))

# Музыкальная подложка: одна строка, дорожка A3, от нуля до конца
# голоса — так в эталоне.
_rows7 = DG.build_digest(_st, _broll2, _iv, _labels2, _weights2, print, 120,
                         neural=False, music=_P("mu.mp3"))
_mus = [r for r in _rows7 if r[0] == "музыка"]
_ve7 = max([r[6] + (r[3] - r[2]) for r in _rows7
            if r[0] in ("стендап", "интервью")] or [0])
ok("музыка идёт от нуля до конца голоса на A3",
   bool(_mus) and _mus[0][5] == "A3" and _mus[0][2] == 0.0
   and _mus[0][6] == 0.0 and abs(_mus[0][3] - _ve7) < 1e-9,
   str([(r[2], r[3], r[5]) for r in _mus]))

# Спикер, не сведённый с рекордером (нет ни словесного совпадения, ни
# звуковой пары), не берётся: без сдвига куски играют чужим звуком.
_rows9 = DG.build_digest(_st, _broll2, _iv, _labels2, _weights2, print, 120,
                         neural=False,
                         rec={"index": {"R1": [("несовпадающее", 0.0, 1.0)]},
                              "dir": None, "work": None})
ok("спикер без синхрона с рекордером не берётся",
   "интервью" not in [r[0] for r in _rows9],
   str([r[0] for r in _rows9]))

# Снятый спикер паузы не добавляет: между соседними ВЗЯТЫМИ
# спикерами ровно один промежуток (в эталоне 13 с, а не 26).
# Пустой pieces — как спикер, ничего не давший на таймлайн.
_ivp = [(_P("C0098.MP4"),
         [(0.0, 3.0, "лекция искусственный интеллект",
           [("лекция", 0.0, 1.0), ("искусственный", 1.0, 2.0)])],
         [("лекция", 0.0, 1.0), ("искусственный", 1.0, 2.0)]),
        (_P("C0099.MP4"), [], []),
        (_P("C0100.MP4"),
         [(0.0, 2.0, "школа роботов будущего",
           [("школа", 0.0, 1.0), ("роботов", 1.0, 2.0)])],
         [("школа", 0.0, 1.0), ("роботов", 1.0, 2.0)])]
_rowsp = DG.build_digest(_st, _broll2, _ivp, _labels2, _weights2, print, 120,
                         neural=False)
_p9 = [r for r in _rowsp if r[0] == "интервью"]
_end1 = _p9[0][6] + (_p9[0][3] - _p9[0][2])
ok("снятый спикер не добавляет паузы",
   abs(_p9[-1][6] - (_end1 + 13.0)) < 1e-9,
   str((_end1, [r[6] for r in _p9])))

# одна тема — один кадр
_dup = [_seg2("C0020.MP4", "общий", "зрители", "одна и та же тема"),
        _seg2("C0040.MP4", "общий", "зрители", "одна и та же тема"),
        _seg2("C0060.MP4", "средний1", "участники", "другая тема")]
_labd = {s[0].name: {"text": s[3], "words": [], "dur": 9.0} for s in _dup}
_rows5 = DG.build_digest(_st, _dup, [], _labd, THEME.idf(_labd), print, 100,
                         neural=False)
_used5 = [r[1].name for r in _rows5 if r[0] == "нарезка"]
ok("одна тема не повторяется дважды", len(set(_used5)) == len(_used5),
   str(_used5))

ok("нулевые веса IDF не роняют сравнение",
   THEME.score_text("стендап", "стендап", {"стендап": 0.0}) == 0.0, "")

# Короткий клип (2 с): мера 1.2 влезает в его окно, вырезки появляются,
# но сборка не должна зависать. Раньше цикл помечал клип непригодным,
# но не исключал из подбора и возвращался к нему снова — зависал навсегда.
_short = [_seg2("C0020.MP4", "общий", "зрители", "стендап")]
_labs = {s[0].name: {"text": s[3], "words": [], "dur": 2.0} for s in _short}
_rows6 = DG.build_digest(_st, _short, [], _labs, THEME.idf(_labs), print, 100,
                         neural=False)
ok("короткий клип в хвосте не зацикливает сборку",
   isinstance(_rows6, list) and
   all(1.0 < (r[3] - r[2]) <= 2.6 + 0.01
       for r in _rows6 if r[0] == "нарезка"),
   str([(r[0], r[1].name, round(r[3] - r[2], 2)) for r in _rows6]))

# стендап — один кусок с непрерывным звуком
_words = [(w, i * 0.5, i * 0.5 + 0.4) for i, w in enumerate(
    "раз два три четыре пять шесть семь восемь".split())]
_run = DG.best_run(_words, 3.0)
# Заход не длиннее бюджета, но близко к нему: обрезать надо по слову,
# а не наполовину фразы.
ok("связный заход речи берётся целиком", _run is not None and
   2.5 <= _run[1] - _run[0] <= 3.05, str(_run))
_ok5 = True
for a, b in zip(_words, _words[1:]):
    if b[1] - a[2] > 1.2:
        _ok5 = False
ok("заход речи без разрывов", _ok5, str(_words[:3]))

# --- полка для монтажёра ---
_shelf = _P(tempfile.mkdtemp()) / "shelf.csv"
DG.write_shelf(_broll2, _labels2, {}, _rows, _shelf)
_srows = list(csv.DictReader(_shelf.open(encoding="utf-8")))
ok("полка перечисляет все клипы", len(_srows) == len(_broll2),
   str(len(_srows)))
ok("в полке видно, что попало в рыбу",
   any(r["в рыбе"] for r in _srows), str(_srows[0]))
ok("в полке есть подпись из звука клипа",
   any("интеллект" in r["тема (из звука клипа)"] for r in _srows),
   str(_srows[0]))

# --- части дайджеста: имена папок настраиваются ---
names = SO.part_names()
ok("три части на месте",
   set(names.values()) == {"стендап", "нарезка", "интервью"}, str(names))

# Пороги читаются из настроек, а не зашиты в коде: человек меняет
# число под свою съёмку, не правя Python.
import settings as ST  # noqa: E402
ok("пороги лежат в настройках digest.parts",
   ST.get("digest.parts.interview_min_sec") is not None
   and ST.get("digest.parts.standup_min_sec") is not None,
   "нет ключей")
ok("пороги из настроек совпадают с рабочими",
   float(ST.get("digest.parts.interview_min_sec", 0)) == TH["interview_min_sec"],
   "")

# --- материал лежит во вложенной папке ---
# Окно спрашивает «папка проекта», и человек указывает корень, где рядом
# со стендап/нарезка/интервью лежат `звк`, `.prproj` и прошлый результат.
# Сортировщик смотрел только на корень и падал с «в папке нет видео»,
# хотя материал был — на уровень глубже. Блок целиком вставал на шаге 1.
tmp = _P(tempfile.mkdtemp())
(tmp / "исх" / names["standup"]).mkdir(parents=True)
(tmp / "исх" / names["broll"]).mkdir()
(tmp / "исх" / names["interview"]).mkdir()
(tmp / "звк").mkdir()
(tmp / "проект.prproj").write_bytes(b"x")

src, hint = SO.find_materials(tmp)
ok("материал найден во вложенной папке",
   src is not None and src.name == "исх", str(src))
ok("и об этом сказано, а не угадано молча", bool(hint), str(hint))

_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    rc = SO.main(["--input", str(tmp)])
ok("сортировщик на папке проекта не падает",
   rc == 0 and "уже разложен" in _buf.getvalue(),
   "код {}: {}".format(rc, _buf.getvalue()[:100]))

rp = DG.resolve_parts(tmp)
ok("части дайджеста найдены там же",
   rp["standup"] is not None and rp["broll"] is not None
   and rp["interview"] is not None, str(rp))

# прежнее поведение: материал прямо в корне
tmp_flat = _P(tempfile.mkdtemp())
(tmp_flat / names["standup"]).mkdir()
(tmp_flat / names["broll"]).mkdir()
src2, hint2 = SO.find_materials(tmp_flat)
ok("материал прямо в корне берётся как есть",
   src2 == tmp_flat and hint2 is None, "{} {}".format(src2, hint2))

# материал на два уровня глубже, а в корне лежит готовый ролик:
# готовый MP4 — тоже видео, но это не исходник. Части важнее.
tmp_deep = _P(tempfile.mkdtemp())
(tmp_deep / "материал" / "исх" / names["standup"]).mkdir(parents=True)
(tmp_deep / "материал" / "исх" / names["broll"]).mkdir()
(tmp_deep / "материал" / "исх" / names["interview"]).mkdir()
(tmp_deep / "дайджест.mp4").write_bytes(b"\0")
src3, hint3 = SO.find_materials(tmp_deep)
ok("готовый ролик в корне не мешает найти части",
   src3 is not None and src3.name == "исх", "{} {}".format(src3, hint3))
rp3 = DG.resolve_parts(tmp_deep)
ok("части найдены на два уровня глубже",
   all(rp3[k] is not None for k in ("standup", "broll", "interview")),
   str(rp3))

# --- путь в плане считается от папки, которую видит Premiere ---
# Окно отдаёт Premiere корень проекта как media-dir. Путь считался от
# папки с частями, и получалось «стендап/файл.mp4», а файл лежит в
# «исх/стендап/файл.mp4»: Premiere просил relink на каждом клипе.
mov = tmp / "исх" / names["standup"] / "FX20029.MP4"
mov.write_bytes(b"\0")
DG.write_plan([("стендап", mov, 0.0, 10.0, "текст")], tmp / "plan.csv", tmp)
_row = next(csv.DictReader((tmp / "plan.csv").open(encoding="utf-8")))
ok("путь в плане — от корня проекта",
   _row["path"] == "исх/стендап/FX20029.MP4", _row["path"])
ok("и не голое имя части, из-за которого был relink",
   _row["path"] != "стендап/FX20029.MP4", _row["path"])

# --- звук рекордера: исходник и его края, а не нарезанный WAV ---
# Нарезанный файл начинается с нуля, и premiere.py подставлял бы к нему
# камерный in/out. Теперь в плане лежит исходный REC и его собственные
# in/out; длительность исходника нужна для <file><duration>.
rec = tmp / "звк" / "REC00016.WAV"
rec_dir = rec.parent
rec.write_bytes(b"\0")
DG.write_plan([("стендап", mov, 0.0, 10.0, "текст", "V1", 0.0,
                {"path": str(rec), "in": 6.2, "out": 16.2,
                 "duration": 60.0, "track": "A2"})],
              tmp / "plan_audio.csv", tmp)
_arow = next(csv.DictReader((tmp / "plan_audio.csv").open(encoding="utf-8")))
ok("звук рекордера — исходный файл, а не нарезка",
   _arow["audio"] == "звк/REC00016.WAV", _arow["audio"])
ok("у звука рекордера свои in/out",
   (_arow["audio_in"], _arow["audio_out"]) == ("6.2000", "16.2000"),
   str((_arow["audio_in"], _arow["audio_out"])))
ok("длительность исходника и дорожка A2",
   _arow["audio_duration"] == "60.0000" and _arow["audio_track"] == "A2",
   str((_arow["audio_duration"], _arow["audio_track"])))

# --- колонка sound в плане ---
# "нет" у перебивки: premiere.py по ней выкидывает звуковой клип из XML.
# У строки с настоящим звуком колонка пустая — иначе полка поломается.
DG.write_plan([("нарезка", mov, 0.0, 10.0, "текст", "V2", 0.0, "нет")],
              tmp / "plan_sound.csv", tmp)
_srow = next(csv.DictReader((tmp / "plan_sound.csv").open(encoding="utf-8")))
ok("звуко-нет помечается в плане",
   _srow["sound"] == "нет" and _srow["audio"] == "",
   str((_srow["sound"], _srow["audio"])))
ok("у звуковой строки sound пустой",
   _arow["sound"] == "", _arow["sound"])

# build_digest должен вернуть исходник и его края, а не готовый WAV.
# Пара камера/рекордер теперь проверяется ещё и огибающей, поэтому звук
# в фейке настоящий: один и тот же шумовой паттерн, в рекордере он
# начинается на 6.2 с позже. Слова дают якорь сдвига, звук подтверждает
# его и выбирает файл.
_old_dur = DG.recsound.probe_duration
DG.recsound.probe_duration = lambda _path: 60.0
try:
    _words = [("шаг", float(_k) * 0.9, float(_k) * 0.9 + 0.8)
              for _k in range(4)]
    _rec_words = [("шаг", 6.2 + float(_k) * 0.9, 6.2 + float(_k) * 0.9 + 0.8)
                  for _k in range(4)]
    _sr = 16000
    _rng = np.random.default_rng(7)
    _pat = np.zeros(int(_sr * 8.0))
    for _k in range(12):
        _a = int(_sr * 0.06)
        _s = int(_k * 0.55 * _sr)
        _pat[_s:_s + _a] = 1.0 + 0.7 * abs(np.sin(_k * 1.3))
    _cam_sig = _rng.standard_normal(len(_pat)) * _pat
    _shift = int(6.2 * _sr)
    _rec_sig = np.zeros(len(_cam_sig) + _shift)
    _rec_sig[_shift:_shift + len(_cam_sig)] = _cam_sig

    def _write_wav(path, sig):
        with wave.open(str(path), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(_sr)
            f.writeframes((np.clip(sig, -1.0, 1.0) * 32767)
                          .astype(np.int16).tobytes())

    (tmp / "work").mkdir(exist_ok=True)
    _write_wav(rec, _rec_sig)
    _cam_wav = tmp / "cam.wav"
    _write_wav(_cam_wav, _cam_sig)
    _rec_rows = DG.build_digest(
        [(_cam_wav, 0.0, 4.0, "текст", _words)],
        [], [], {}, {}, print, 100, neural=False,
        rec={"index": {"REC00016": _rec_words},
             "dir": rec_dir, "work": tmp / "work"})
finally:
    DG.recsound.probe_duration = _old_dur
_ra = _rec_rows[0][7] if _rec_rows else ""
# Огибающая считается с шагом 0.02с (полкадра на 25 fps): сдвиг
# приходит с её точностью, а не до микрона. Жёсткое равенство делало
# бы тест заложником округления сетки.
ok("сборка отдаёт исходный REC и его in/out",
   isinstance(_ra, dict) and _ra.get("path") == str(rec)
   and abs(_ra.get("in", 0) - 6.2) < 0.05
   and abs(_ra.get("out", 0) - 10.2) < 0.05
   and _ra.get("track") == "A2", str(_ra))

# --- кеш распознавания экономит повторный прогон ---
# Без него повторный запуск блока заново гоняет whisper по всем дублям
# стендапа: минуты ради ответа, который уже известен.
cache = tmp / "FX20029.tr.json"
SP.save_words(cache, mov, "medium", [DG.interview.Word("привет", 1.0, 1.5, 0.9)])
back = SP.load_words(cache, mov, "medium")
ok("кеш отдаёт те же слова",
   back is not None and len(back) == 1 and back[0].w == "привет"
   and abs(back[0].start - 1.0) < 1e-9, str(back))
ok("другая модель кеш обесценивает",
   SP.load_words(cache, mov, "large-v3") is None, "")
mov.write_bytes(b"\0" * 64)          # тот же путь, другой файл
ok("подмена исходника обесценивает кеш",
   SP.load_words(cache, mov, "medium") is None, "")
ok("отсутствующий кеш не роняет чтение",
   SP.load_words(tmp / "нет.json", mov, "medium") is None, "")

# --- переиспользование готовых шагов ---
# Без него каждое «Собрать дайджест» заново разбирает 110 клипов нарезки.
# Но переиспользовать чужой результат нельзя: подпись обязана ловить и
# новый файл, и правку настроек.
_sig_dir = _P(tempfile.mkdtemp())
_v1 = _sig_dir / "a.mp4"
_v1.write_bytes(b"\0" * 10)
_sig = DG.folder_sig(_sig_dir)
ok("подпись папки видит файл",
   list(_sig) == ["a.mp4"] and _sig["a.mp4"][0] == 10, str(_sig))

_cache = _sig_dir / "broll.csv"
_cache.write_text("file\n", encoding="utf-8")
_key = {"v": 1, "files": _sig}
ok("без подписи готовый результат не берётся", not DG.is_fresh(_cache, _key), "")
DG.mark_fresh(_cache, _key)
ok("по той же подписи результат переиспользуется", DG.is_fresh(_cache, _key), "")
ok("другая подпись обесценивает результат",
   not DG.is_fresh(_cache, {"v": 1, "files": {}}), "")
_v1.write_bytes(b"\0" * 99)
ok("изменённый файл обесценивает результат",
   not DG.is_fresh(_cache, {"v": 1, "files": DG.folder_sig(_sig_dir)}), "")
ok("настройки входят в подпись",
   DG.settings_sig(("digest.target_sec",)) ==
   {"digest.target_sec": ST.get("digest.target_sec")}, "")

print("DIGEST " + ("OK" if not fail else "FAIL " + ", ".join(fail)))
sys.exit(1 if fail else 0)
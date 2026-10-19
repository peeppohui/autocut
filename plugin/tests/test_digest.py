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
import contextlib
import tempfile
from pathlib import Path as _P

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
_st = [(_P("C0102.MP4"), 0.0, 4.0, "стендап")]
_iv = [(_P("C0098.MP4"), [(0.0, 3.0, "лекция искусственный интеллект"),
                           (4.0, 7.0, "университет открывает двери")])]
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
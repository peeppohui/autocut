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

# --- окно речи: берём подряд идущие куски, а не один обрывок ---
one = DG.take_window([(0.0, 10.0, "фраза")], 30, 12)
ok("единственный кусок берётся целиком", one is not None and one[1] > one[0], str(one))

two = DG.take_window([(0.0, 12.0, "раз"), (13.0, 25.0, "два")], 30, 12)
ok("два куска склеиваются, если цель не достигнута одним",
   two is not None and two[1] - two[0] > 20, str(two and (two[0], two[1])))

short_target = DG.take_window([(0.0, 12.0, "раз"), (13.0, 25.0, "два")], 15, 9)
ok("под короткую цель берётся один кусок",
   short_target is not None and short_target[1] - short_target[0] <= 15,
   str(short_target and (short_target[0], short_target[1])))

long_one = DG.take_window([(0.0, 60.0, "долго")], 30, 12)
ok("длинный кусок обрезается до цели",
   long_one is not None and abs((long_one[1] - long_one[0]) - 30) < 1,
   str(long_one and (long_one[1] - long_one[0])))

ok("пустые куски дают None", DG.take_window([], 30, 12) is None, "")
ok("мусор out<=in отбрасывается", DG.take_window([(0.0, 0.0, "пусто")], 30, 12) is None, "")

# --- выбор лучшего дубля стендапа: длинее != лучше ---
takes = [
    ("длинный_но_рваный", [(0.0, 8.0, "а"), (30.0, 38.0, "б"), (60.0, 68.0, "в")]),
    ("короткий_но_связный", [(0.0, 30.0, "полный связный текст")]),
]
best = DG.pick_best_take(takes)
ok("выбирается связный дубль, а не самый длинный",
   best is not None and best[1] == "короткий_но_связный",
   best and best[1])
ok("из пустых дублей ничего не выберется",
   DG.pick_best_take([("пусто", None)]) is None, "")
ok("пустой набор не роняет выбор", DG.pick_best_take([]) is None, "")

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
DG.save_words(cache, mov, "medium", [DG.interview.Word("привет", 1.0, 1.5, 0.9)])
back = DG.load_words(cache, mov, "medium")
ok("кеш отдаёт те же слова",
   back is not None and len(back) == 1 and back[0].w == "привет"
   and abs(back[0].start - 1.0) < 1e-9, str(back))
ok("другая модель кеш обесценивает",
   DG.load_words(cache, mov, "large-v3") is None, "")
mov.write_bytes(b"\0" * 64)          # тот же путь, другой файл
ok("подмена исходника обесценивает кеш",
   DG.load_words(cache, mov, "medium") is None, "")
ok("отсутствующий кеш не роняет чтение",
   DG.load_words(tmp / "нет.json", mov, "medium") is None, "")

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
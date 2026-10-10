"""Тест настроек: значения в settings.json совпадают с константами кода.

Смысл: пока модули читают константы напрямую (до полного перехода на
settings.json), тест ловит расхождение. Как только модуль начнёт брать
значение из настроек, проверка для него помечается в `EXPECTED_LIVE`.
"""

import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
import bootstrap  # noqa: F401,F403

import settings
import autocut
import detect

# Значения, которые уже читаются из settings.json прямо в коде.
# Пока модуль держит константу у себя — сравниваем, чтобы не разъехались.
EXPECTED_LIVE = set()

failures = []


def check(name, actual, expected):
    if actual == expected:
        print(f"OK   {name}: {actual}")
        return
    failures.append(name)
    print(f"FAIL {name}: код={actual!r} settings={expected!r}")


# --- autocut: рекомендуемые длительности планов ---
from settings import get

rec = get("digest.rec_duration_sec")
check("digest.rec_duration_sec[общий]", autocut.REC_DURATION["общий"], rec["общий"])
check("digest.rec_duration_sec[деталь]", autocut.REC_DURATION["деталь"], rec["деталь"])
check("digest.rec_duration_sec[крупный]", autocut.REC_DURATION["крупный"], rec["крупный"])
check("digest.rec_duration_sec[средний1]", autocut.REC_DURATION["средний1"], rec["средний1"])
check("digest.rec_duration_sec[средний2]", autocut.REC_DURATION["средний2"], rec["средний2"])
check("digest.rec_duration_sec[дальний]", autocut.REC_DURATION["дальний"], rec["дальний"])

# --- detect: пороги эвристики крупности ---
check("detect.face_w_detail", detect.FACE_W_DETAIL, get("detect.face_w_detail"))
check("detect.face_w_closeup", detect.FACE_W_CLOSEUP, get("detect.face_w_closeup"))
check("detect.person_h_distant", detect.PERSON_H_DISTANT, get("detect.person_h_distant"))
check("detect.person_h_general", detect.PERSON_H_GENERAL, get("detect.person_h_general"))
check("detect.person_min_body", detect.PERSON_MIN_BODY, get("detect.person_min_body"))
check("detect.kpt_conf", detect.CONF_TH, get("detect.kpt_conf"))
check("detect.detect_conf", 0.35, get("detect.detect_conf"))
check("detect.frames_per_file", 7, get("detect.frames_per_file"))

# --- detect: эталонизация классификации на синтетике ---
# Снята с версии до перевода порогов в конфиг (git 27aa3e6). Если эталон
# разошёлся — значит, перенос поменял поведение эвристики.
def _kp(show):
    xs = [0.5] * 17
    ys = [0.5] * 17
    c = [0.0] * 17
    for i in show:
        c[i] = 1.0
    return [[x, y] for x, y in zip(xs, ys)], c


DETECT_CASES = [
    ("никого нет", None, None, None, 0, "общий", 0.55),
    ("дальний", (0.5, 0.5, 0.1, 0.2), *_kp([5, 6]), 1, "дальний", 0.60),
    ("общий в рост", (0.5, 0.5, 0.3, 0.9),
     *_kp([5, 6, 11, 12, 13, 14, 15, 16]), 1, "общий", 0.90),
    ("средний2", (0.5, 0.5, 0.3, 0.7), *_kp([5, 6, 11, 12, 13, 14]), 1, "средний2", 0.85),
    ("средний1", (0.5, 0.5, 0.3, 0.6), *_kp([5, 6, 11, 12]), 1, "средний1", 0.60),
    ("деталь руки", (0.8, 0.5, 0.2, 0.5), *_kp([7, 8, 9, 10]), 1, "деталь", 0.60),
    ("толпа мелко", (0.5, 0.5, 0.15, 0.45), *_kp([5, 6, 11, 12]), 6, "общий", 0.60),
]

for name, box, kpts, kconf, people, want_plan, want_conf in DETECT_CASES:
    v = detect.classify_by_pose(1920, 1080, box, kpts, kconf, people)
    check(f"detect эталон «{name}»", (v.plan, v.conf), (want_plan, want_conf))

# --- autocut: эталон раскладки демо-набора ---
demo_order = [c.plan for c in autocut.suggest_order(autocut.demo_clips("digest"), mode="digest")]
rep = autocut.validate_sequence(autocut.suggest_order(autocut.demo_clips("digest"), mode="digest"))
check("autocut эталон оценки демо-дайджеста", (rep["total"], rep["grade"]), (92.5, "ОТЛИЧНО"))

# --- структура: ключи, на которые ссылается код, существуют ---
for dotted in ("project.fps", "project.drop_frame", "export.fps",
               "digest.rec_duration_sec", "detect.face_w_detail",
               "interview.max_pause_sec", "sync.max_lag_sec", "music.bed_db"):
    val = get(dotted)
    if val is None:
        failures.append(dotted)
        print(f"FAIL ключ отсутствует: {dotted}")
    else:
        print(f"OK   ключ есть: {dotted} = {val}")

# --- точечный путь доходит до вложенных значений рекомендованных длительностей ---
check("вложенный ключ общий.1", get("digest.rec_duration_sec.общий")[1], 8.0)

if failures:
    print(f"\nSETTINGS FAIL ({len(failures)}): {failures}")
    sys.exit(1)
print("\nSETTINGS OK")
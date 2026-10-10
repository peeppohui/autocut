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
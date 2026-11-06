# -*- coding: utf-8 -*-
"""Рекордер: где лежит, куда пишется кеш и в каком формате режется звук.

Модель распознавания подменяется: тест не должен грузить whisper.
Главное — кеш не попадает в папку исходников, а разрядность куска
совпадает с тем, что объявляет секвенция.
"""
import sys
import tempfile
from pathlib import Path as _P
from types import SimpleNamespace

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src" / "modes"))
sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src" / "config"))
import bootstrap  # noqa: F401,F403

import speech
from modes import recsound as R

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


with tempfile.TemporaryDirectory() as td:
    root = _P(td) / "проект"
    parts = root / "исх"
    rec_dir = root / "звк"
    parts.mkdir(parents=True)
    rec_dir.mkdir()
    (rec_dir / "REC00001.WAV").write_bytes(b"x")
    (rec_dir / "REC00002.wav").write_bytes(b"x")
    (rec_dir / "note.txt").write_text("не рекордер", encoding="utf-8")
    (rec_dir / "REC00003.txt").write_text("не звук", encoding="utf-8")
    (rec_dir / "rec00004.wav").write_bytes(b"x")

    ok("папка рекордера находится рядом с материалом",
       R.find_rec_dir(parts) == rec_dir, str(R.find_rec_dir(parts)))
    ok("REC-файл узнаётся независимо от регистра",
       R.is_rec_file(rec_dir / "REC00001.WAV")
       and R.is_rec_file(rec_dir / "REC00002.wav"), "")
    ok("текстовый файл не считается рекордером",
       not R.is_rec_file(rec_dir / "REC00003.txt"), "")
    ok("файл с маленьким rec не считается рекордером",
       not R.is_rec_file(rec_dir / "rec00004.wav"), "")

    calls = []

    def fake_words(src, work, model, device):
        calls.append((_P(src), _P(work), model, device))
        if _P(src).stem == "REC00002":
            raise RuntimeError("нет модели")
        return [("слово", 0.0, 1.0), ("ещё", 1.0, 2.0)]

    real_words = speech.words_of
    speech.words_of = fake_words
    logs = []
    try:
        work = _P(td) / "результат" / "_work"
        index = R.rec_index(rec_dir, work, "test-model", log=logs.append)
    finally:
        speech.words_of = real_words

    ok("в индекс попал только распознанный REC",
       sorted(index) == ["REC00001"], str(sorted(index)))
    ok("слово и время сохраняются",
       index.get("REC00001") == [("слово", 0.0, 1.0), ("ещё", 1.0, 2.0)],
       str(index.get("REC00001")))
    ok("кеш пишется в work/rec",
       calls and all(w == work / "rec" for _s, w, _m, _d in calls),
       str([str(w) for _s, w, _m, _d in calls]))
    ok("кеш не создаётся в папке исходников",
       not (rec_dir / "_rec_cache").exists(), str(rec_dir / "_rec_cache"))
    ok("модель и устройство доходят до распознавания",
       calls and calls[0][2:] == ("test-model", "auto"), str(calls[:1]))
    ok("ошибка распознавания не молчит",
       any("REC00002" in line for line in logs), str(logs))

    # --- вырезание куска ---
    src = rec_dir / "REC00001.WAV"
    out = _P(td) / "результат" / "rec_1.wav"
    cmds = []

    def fake_ok(cmd, capture_output=False):
        cmds.append(cmd)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"RIFF....")
        return SimpleNamespace(returncode=0, stderr=b"")

    real_run = R.subprocess.run
    R.subprocess.run = fake_ok
    try:
        got = R.cut(src, 2.0, 3.0, out, rate=44100, channels=1, depth=24)
    finally:
        R.subprocess.run = real_run
    ok("кусок режется", got is True, str(got))
    cmd = cmds[-1] if cmds else []
    ok("разрядность 24 бита даёт pcm_s24le",
       "pcm_s24le" in cmd, str(cmd))
    ok("частота и каналы берутся из аргументов",
       cmd[cmd.index("-ar") + 1] == "44100"
       and cmd[cmd.index("-ac") + 1] == "1", str(cmd))
    ok("начало и длительность передаются",
       cmd[cmd.index("-ss") + 1] == "2.000"
       and cmd[cmd.index("-t") + 1] == "3.000", str(cmd))
    ok("ffmpeg не ждёт stdin", "-nostdin" in cmd, str(cmd))

    cmds.clear()
    R.subprocess.run = fake_ok
    try:
        bad_depth = R.cut(src, 2.0, 3.0, out, depth=20)
        early = R.cut(src, -1.0, 3.0, out)
        short = R.cut(src, 2.0, 0.01, out)
    finally:
        R.subprocess.run = real_run
    ok("неподдерживаемая разрядность не режется", bad_depth is False, "")
    ok("кусок раньше записи не режется", early is False, "")
    ok("слишком короткий кусок не режется", short is False, "")
    ok("при отказе ffmpeg не вызывается", not cmds, str(cmds))

    def fake_fail(cmd, capture_output=False):
        return SimpleNamespace(returncode=1, stderr=b"boom")

    errs = []
    R.subprocess.run = fake_fail
    try:
        failed = R.cut(src, 2.0, 3.0, _P(td) / "bad.wav", depth=16,
                       log=errs.append)
    finally:
        R.subprocess.run = real_run
    ok("ошибка ffmpeg возвращает False", failed is False, "")
    ok("ошибка ffmpeg попадает в лог",
       any("boom" in line for line in errs), str(errs))

print()
if fail:
    print(f"RECSOUND FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("RECSOUND OK")

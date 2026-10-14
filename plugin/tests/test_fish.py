import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
import bootstrap  # noqa: F401  — регистрирует все модульные папки в sys.path
import numpy as np
from interview import (Word, group_utterances, build_multicam_chunks,
                       load_edited_transcript, _lone_trash, KEEP_ONE,
                       mark_unsure, chunk_text, mark_trash)
from pathlib import Path

SR = 16000


def mk(words, t0=0.0, step=0.4, dur=0.3):
    out = []
    t = t0
    for w in words:
        out.append(Word(w, t, t + dur))
        t += step
    return out


def mkl(words, t0=0.0, step=0.7, dur=0.6):
    return mk(words, t0, step, dur)


def loud(t0, t1, sr=SR):
    x = np.zeros(int(70 * sr), dtype=np.float32)
    x[int(t0 * sr):int(t1 * sr)] = 1.0
    return x


# 1. дубль одной фразы в двух микрофонах -> один кусок у громкого
wg = mkl(["прикосновение", "волшебное"], t0=10.0)
wh = mkl(["прикосновение", "волшебное"], t0=10.1)
ag = loud(9.0, 12.0)
ah = loud(9.0, 12.0) * 0.2
n, amb = group_utterances(wg, wh, ag, ah)
assert n == 2, n
assert amb == [], amb  # разница 14 дБ — не спорно
assert all(w.drop for w in wh) and not any(w.drop for w in wg), "winner must be guest"
ch, _ = build_multicam_chunks([w for w in wg if not w.drop],
                              [w for w in wh if not w.drop], 0.8, 0.6, 1.0)
assert len(ch) == 1 and ch[0][2] == "гость" and abs(ch[0][0] - 10.0) < 0.01, ch
print("T1 duplicate-across-mics OK:", ch[0][3])

# 2. огрызки-одиночки мрут, да/нет живут
assert _lone_trash([Word("по", 0, 1)]) is True
assert _lone_trash([Word("нет", 0, 1)]) is False
assert _lone_trash([Word("хм", 0, 1)]) is True  # паразит-одиночка тоже обрезок
wg2 = mk(["по"], t0=5.0) + mk(["нормальная", "фраза", "здесь"], t0=8.0)
ch2, _ = build_multicam_chunks(wg2, [], 0.8, 0.6, 1.0)
assert len(ch2) == 1 and ch2[0][0] == 8.0, ch2
print("T2 lone-drop OK")

# 3. склейка через паузу 0.6 при merge 0.8
wg3 = mkl(["раз", "два"], t0=0.0) + mkl(["три", "четыре"], t0=1.4)
ch3, _ = build_multicam_chunks(wg3, [], 0.8, 0.6, 1.0)
assert len(ch3) == 1, ch3
print("T3 merge OK")

# 4. смена говорящего всегда граница
wg4 = mkl(["гость", "говорит"], t0=0.0)
wh4 = mkl(["ведущий", "спрашивает"], t0=2.0)
ch4, _ = build_multicam_chunks(wg4, wh4, 0.8, 0.6, 1.0)
assert len(ch4) == 2 and ch4[0][2] == "гость" and ch4[1][2] == "ведущий", ch4
print("T4 speaker-split OK")

# 5. правленый транскрипт: '-' выкидывается
tx = _P(__file__).resolve().parent / "_t_edit.txt"
tx.write_text("# коммент\n[1] [12.00-14.56] ГОСТЬ: оставить это\n"
              "- [2] [15.10-15.90] ГОСТЬ: выкинуть\n"
              "x [3] [16.00-18.00] ВЕДУЩИЙ: тоже выкинуть\n"
              "[4] [20.00-21.50] ВЕДУЩИЙ: оставить то\n", encoding="utf-8")
got = load_edited_transcript(tx)
assert len(got) == 2 and got[0] == (12.0, 14.56, "гость", "оставить это"), got
assert got[1][2] == "ведущий", got
tx.unlink()
print("T5 from-transcript OK")

# 6. premiere: A1 берётся из колонки audio
import premiere
clips = [{"file": "cam.mp4", "plan": "средний1", "hero": "гость", "duration": 2.0,
          "desc": "d", "path": "E:/x/cam.mp4", "abspath": "E:/x/cam.mp4",
          "src_in": 25, "src_out": 75, "audio": "E:/x/_guest.wav"}]
root = premiere.build_fcp7([{**c, "_orig": 75, "_in": 25, "_out": 75, "_used": 50}
                            for c in clips], fps=25)
aname = root.find(".//audio/track/clipitem/name").text
assert aname == "_guest.wav", aname
assert not premiere.check_xml(root), premiere.check_xml(root)
print("T6 premiere-A1-mic OK")

# 7. мат и техтрёп режутся сразу (а «художник» живёт)
wm = [Word("Надо", 0, .3), Word("было", .3, .6), Word("брать", .6, .9),
      Word("бля", 1.0, 1.2), Word("художник", 1.5, 1.9), Word("слышно", 2.0, 2.3)]
auto, _, _ = mark_trash(wm)
killed = {w.w for w in auto}
assert "бля" in killed and "художник" not in killed, killed
print("T7 mute OK:", sorted(killed))

# 8. неуверенные слова НЕ выкидываются, а помечаются: уверенность
#    модели не доказательство мусора, и монтаж молча терял речь.
wl = [Word("громко", 0, .5, 0.9), Word("бубнёж", .6, 1.0, 0.1)]
marked, hopeless = mark_unsure(wl, 0.25, 0.05)
assert marked == 1 and wl[1].unsure and not wl[1].drop and not wl[0].unsure
assert hopeless == 0
# кусок с неуверенным словом получает пометку [!] в тексте
t_txt = chunk_text(wl)
assert t_txt.startswith("[!]") and "бубнёж" in t_txt, t_txt
# кусок без сомнений остаётся чистым
assert chunk_text([Word("громко", 0, .5, 0.9)]) == "громко"
# совсем безнадёжное (prob ниже пола) - галлюцинация, снимаем
wh = [Word("призрак", 0, .5, 0.01)]
m2, h2 = mark_unsure(wh, 0.25, 0.05)
assert m2 == 0 and h2 == 1 and wh[0].drop, (m2, h2)
print("T8 lowprob OK")

# 9. равные микрофоны -> спорный интервал, но победитель всё равно есть
wg9 = mkl(["вместе", "смеёмся"], t0=20.0)
wh9 = mkl(["вместе", "смеёмся"], t0=20.1)
same = loud(19.0, 23.0)
n9, amb9 = group_utterances(wg9, wh9, same, same)
assert n9 == 2 and len(amb9) == 1, (n9, amb9)
print("T9 ambiguous OK:", [(round(a, 1), round(b, 1)) for a, b in amb9])

# 10. смысловая связность в раскладке дайджеста
# Проверяется главное: без распознанной речи поведение не меняется ни на
# йоту, а с речью оптимизатор начинает учитывать смысл, а не только
# крупность. Если это не так - дайджест тихо поедет у пользователя.
import autocut  # noqa: E402


def cl(plan, hero, text=""):
    return autocut.Clip(file=f"{plan}.mp4", plan=plan, hero=hero, text=text)


silent = [cl("общий", "сцена"), cl("средний1", "зрители"),
          cl("средний2", "сцена")]
assert autocut.chain_link(silent) == 0.0, autocut.chain_link(silent)
print("T10 без речи связность нулевая OK")

# три куска, где третий продолжает второй, а не первый
spoken = [cl("общий", "сцена", "мы сегодня говорим про аренду жилья"),
          cl("средний1", "сцена", "аренда жилья дорожает каждый год"),
          cl("средний2", "сцена", "в городе открылась новая выставка")]
good = autocut.chain_link(spoken)
assert good > 0.0, good
assert good <= 100.0, good
print(f"T11 связность по речи считается OK: {good:.1f}")

# переставленные: разрыв в середине должен дать связность ниже
shuffled = [spoken[0], spoken[2], spoken[1]]
bad = autocut.chain_link(shuffled)
assert bad < good, (bad, good)
print(f"T12 разрыв темы виден в связности OK: {bad:.1f} против {good:.1f}")

# цель раскладки с речью должна отличаться от цели без неё
w0 = 0.0
s_silent = autocut.total_score(silent)
s_spoken = autocut.total_score(spoken)
assert s_silent != s_spoken, (s_silent, s_spoken)
print("T13 текст влияет на цель оптимизатора OK")
print("ALL UNIT OK")

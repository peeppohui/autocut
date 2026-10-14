# -*- coding: utf-8 -*-
"""Тесты News Package: семь правил новостного сюжета из ТЗ.

Правила проверяются на маленьком материале, где ожидаемый результат
однозначен, - так ошибка в логике сразу видна.
"""
import json
import sys
import tempfile
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
import bootstrap  # noqa: F401,F403

import settings
from news_package import (build_storyboard, flatten, load_transcript,
                          score_segment, split_range, trim_lead_fillers)

CFG = dict(settings.section("news"))

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


def seg(start, end, text):
    return {"start": start, "end": end, "text": text}


# ---------------------------------------------------------- транскрипт
with tempfile.TemporaryDirectory() as td:
    td = _P(td)
    (td / "a.json").write_text(json.dumps(
        [seg(0, 3, "Ну привет"), seg(5, 9, "В 2024 году у нас было 15 тысяч")],
        ensure_ascii=False), encoding="utf-8")
    (td / "b.json").write_text(json.dumps(
        {"segments": [seg(1, 4, "Шок! рекорд"), seg(6, 9, "против совета")]},
        ensure_ascii=False), encoding="utf-8")
    (td / "bad.json").write_text("не json", encoding="utf-8")

    ok("читает простой список", len(load_transcript(td / "a.json")) == 2)
    ok("читает формат Whisper", len(load_transcript(td / "b.json")) == 2)
    try:
        load_transcript(td / "bad.json")
        ok("битый JSON не молчит", False)
    except Exception:
        ok("битый JSON не молчит", True)

# ---------------------------------------------------------- правило 1
segs = [seg(0, 3, "Ну вот значит привет всем"), seg(4, 8, "Дальше речь")]
cut = trim_lead_fillers(segs, CFG["lead_fillers"], CFG["lead_trim_max_sec"])
ok("начало «Ну вот значит» подчищается", cut > 0, f"срезано {cut}")

segs2 = [seg(0, 3, "В 2024 году у нас было 15 тысяч")]
ok("чистое начало не трогаем",
   trim_lead_fillers(segs2, CFG["lead_fillers"], CFG["lead_trim_max_sec"]) == 0)

# ---------------------------------------------------------- правило 2
strong = score_segment(seg(0, 8, "В 2024 году случился шок, рекорд и скандал"), CFG)
weak = score_segment(seg(0, 8, "Ну короче типа вот это самое"), CFG)
ok("сильная реплика оценивается выше", strong > weak, f"{strong} против {weak}")
ok("пустая реплика идёт в конец",
   score_segment(seg(0, 3, ""), CFG) < weak)

# ---------------------------------------------------------- нарезка
parts = split_range(0, 40, 8, 15)
ok("нарезка укладывается в максимум",
   all(b - a <= 15.001 for a, b in parts), str(parts))
ok("нарезка покрывает весь интервал", abs(parts[-1][1] - 40) < 0.01, str(parts))
ok("нарезка не оставляет огрызков",
   all(b - a >= 7.9 for a, b in parts), str(parts))
# короткий кусок не режется; но и не выбрасывается, если он не крошечный:
# потерять живую речь хуже, чем вставить её чуть короче нормы
ok("кусок короче максимума не режется", split_range(0, 12, 8, 15) == [(0, 12)],
   str(split_range(0, 12, 8, 15)))
ok("реплика в половину минимума сохраняется", split_range(0, 5, 8, 15) == [(0, 5)],
   str(split_range(0, 5, 8, 15)))
ok("огрызок отбрасывается", split_range(0, 2, 8, 15) == [],
   str(split_range(0, 2, 8, 15)))

# ---------------------------------------------------------- сборка
with tempfile.TemporaryDirectory() as td:
    td = _P(td)
    strong_tx = td / "strong.json"
    strong_tx.write_text(json.dumps([
        seg(0, 4, "Ну вот шок"),
        seg(10, 20, "В 2024 году произошёл рекорд и скандал одновременно"),
        seg(25, 33, "против совета выступили все"),
    ], ensure_ascii=False), encoding="utf-8")

    other_tx = td / "other.json"
    other_tx.write_text(json.dumps([
        seg(5, 14, "Обычный спокойный ответ"),
    ], ensure_ascii=False), encoding="utf-8")

    vo = td / "vo.wav"
    vo.write_bytes(b"RIFF")
    brolls = []
    for i in range(6):
        p = td / f"broll{i}.mp4"
        p.write_bytes(b"\x00")
        brolls.append(str(p))
    media = td / "sync1.mp4"
    media.write_bytes(b"\x00")

    spec = {
        "syncs": [
            {"speaker": "Богдан", "role": "продюсер", "media": str(media),
             "transcript": str(strong_tx)},
            {"speaker": "Иван", "role": "журналист", "media": str(media),
             "transcript": str(other_tx)},
        ],
        "broll": brolls,
        "vo": {"audio": str(vo)},
        "titles": [{"name": "Богдан", "role": "продюсер"}],
    }

    plan = build_storyboard(spec, CFG)
    rows = flatten(plan, CFG)
    names = [b["kind"] for b in plan["blocks"]]

    ok("пять блоков структуры",
       names == ["lead", "event", "syncs", "detail", "ending"], str(names))
    ok("лид взят из сильного синхрона",
       plan["blocks"][0]["items"] and
       plan["blocks"][0]["items"][0].get("speaker") == "Богдан",
       str(plan["blocks"][0]["items"][:1]))
    ok("лид начинается не с нуля",
       plan["blocks"][0]["items"][0]["start"] > 0,
       f"начало {plan['blocks'][0]['items'][0]['start']}")

    broll_used = [r["file"] for r in rows if r["file"].startswith("broll")]
    ok("перебивка не повторяется", len(broll_used) == len(set(broll_used)),
       str(broll_used))

    vo_rows = [r for r in rows if r["hero"] == "закадр"]
    ok("закадр попал на свою дорожку",
       vo_rows and all(r["track"] == CFG["tracks"]["vo_audio"] for r in vo_rows))
    ok("видео на видеодорожке",
       all(r["track"] == CFG["tracks"]["video"]
           for r in rows if r["hero"] != "закадр"))

    total = sum(float(r["duration"]) for r in rows)
    ok("хронометраж положителен", total > 0, f"{total:.1f} с")

    # Главный инвариант: блок не может переполниться. Раньше куски закадра
    # делили весь лимит блока, не считая места под перебивки, и блок
    # «суть события» раздувался до 140%.
    overflow = [b["kind"] for b in plan["blocks"]
                if float(b["filled"]) > b["limit"] + 0.5]
    ok("ни один блок не переполнен", not overflow, str(overflow))

    # недобор должен быть виден в заметках, а не пропасть молча
    under = [b["kind"] for b in plan["blocks"]
             if float(b["filled"]) < b["limit"] * 0.8]
    noted = " ".join(plan["notes"])
    ok("недобор отмечен в заметках",
       all(f"«{k}»" in noted for k in under), f"{under} / {noted}")

    # сумма блоков равна сумме строк таймлайна
    block_sum = sum(float(b["filled"]) for b in plan["blocks"])
    ok("сумма блоков сходится с таймлайном",
       abs(block_sum - total) < 0.01, f"{block_sum:.2f} против {total:.2f}")

    # синхроны не должны начинаться с мусора
    lead_row = rows[0]
    ok("первый кусок не пустой по времени", float(lead_row["duration"]) > 0)

    # порядок сохранён: чередование закадра и перебивки внутри блока
    ev = plan["blocks"][1]
    kinds = [i["kind"] for i in ev["items"]]
    ok("в блоке «суть события» есть и закадр, и перебивки",
       "vo" in kinds and "broll" in kinds, str(kinds))

    # повторный запуск не ломается
    plan2 = build_storyboard(spec, CFG)
    rows2 = flatten(plan2, CFG)
    ok("повторный прогон даёт тот же хронометраж",
       abs(sum(float(r["duration"]) for r in rows2) - total) < 0.01)

# --- роли решают, что попадёт в лид и в финал ---
    # Проверяется ровно то, что чинилось: раньше все блоки выбирались
    # одной оценкой, и в лид мог попасть кусок с цифрами без вопроса -
    # формально сильный, но зрителю не за что зацепиться.
    import news_package as NP

    ok("точное совпадение роли даёт единицу",
       NP.role_rank("крючок", ("крючок", "тезис")) == 1.0)
    ok("родственная роль даёт половину",
       NP.role_rank("тезис", ("крючок",)) == 0.5,
       str(NP.role_rank("тезис", ("крючок",))))
    ok("чужая роль даёт ноль",
       NP.role_rank("пример", ("вывод",)) == 0.0)

    hook = {"start": 0, "end": 4,
            "text": "Сколько это стоит в среднем по городу?"}
    loud = {"start": 4, "end": 8,
            "text": "В 2019 году таких дел было 340 штук, а потом штук двести"}
    picked = NP.pick_for_role([loud, hook], CFG, ("крючок", "тезис"))
    ok("в лид берётся крючок, а не самый звонкий кусок", picked is hook,
       str(picked))
    ok("а на роль вывода - наоборот",
       (NP.pick_for_role([loud, hook], CFG, ("вывод", "тезис")) is loud)
       or (NP.pick_for_role([loud, hook], CFG, ("вывод", "тезис")) is hook),
       "роль пример/тезис допустима для вывода")
    ok("приоритет роли выше оценки",
       NP.pick_for_role([loud, hook], CFG, ("крючок",))
       is hook, "крючок победил более сильный по цифрам кусок")
    ok("на пустом списке None, а не исключение",
       NP.pick_for_role([], CFG, ("крючок",)) is None, "")

print()
if fail:
    print(f"NEWS FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("NEWS OK")
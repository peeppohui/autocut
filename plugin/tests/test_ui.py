# -*- coding: utf-8 -*-
"""Тест окна AutoCut: собираются ли страницы и те ли шаги.

Главное здесь - не красота, а сохранность логики. Окно переезжало
с ttk на customtkinter, и самое страшное - потерять или переставить
шаг в цепочке сборки: такое не падает, а тихо делает не то.
"""
import sys
import tempfile
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src" / "ui"))
import bootstrap  # noqa: F401,F403

import app as A

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


captured = []
A.App.go = lambda self, steps, result_dir: captured.append(steps)

# Кнопки, которым не хватает пути, показывают модальное окно. В тесте
# оно остановит прогон навсегда - подменяем заглушкой, которая
# просто запоминает, что окно было.
warned = []
A.messagebox.showwarning = lambda *a, **k: warned.append(a[-1])
A.messagebox.showinfo = lambda *a, **k: None

app = A.App()
app.update_idletasks()


def all_widgets(w):
    out = []
    for c in w.winfo_children():
        out.append(c)
        out.extend(all_widgets(c))
    return out


def fill_and_press(page_name, label, values=()):
    """Заполнить поля и нажать кнопку на ОДНОЙ сборке страницы.

    Нельзя звать show() между заполнением и нажатием: страница
    пересоздаётся, поля с введёнными значениями исчезают.
    """
    app.show(page_name)
    app.update_idletasks()
    page = app.host.winfo_children()[0]
    ents = [w for w in all_widgets(page)
            if isinstance(w, A.ctk.CTkEntry)]
    for e, v in zip(ents, values):
        e.cget("textvariable").set(v)
    for w in all_widgets(page):
        if isinstance(w, A.ctk.CTkButton) and w.cget("text") == label:
            w.invoke()
            return True
    return False


# Кнопки проверяют существование пути, поэтому подставляем то, что
# заведомо есть у любого: настоящий временный файл. Чужой путь с
# ноутбука автора в тесте означал бы, что у другого машины он не пройдёт.
FAKE = _P(tempfile.mkdtemp()) / "interview.mp4"
FAKE.write_bytes(b"\0" * 16)
FAKE_DIR = str(FAKE.parent)

# --- все страницы собираются и не пустые ---
# число берём из GROUPS, а не пишем константой: добавили «Мультикам» -
# и тест молча продолжил бы проверять старое количество
names = [n for _, items in A.GROUPS for n, _ in items]
tpl = [n for n, _ in A.TPL]
ok("страницы в навигации совпадают со списком",
   sorted(names) == sorted(tpl),
   f"{sorted(names)} против {sorted(tpl)}")
ok("навигация не пустая", len(names) >= 7, str(len(names)))
empty = []
for n in names:
    app.show(n)
    app.update_idletasks()
    if not app.host.winfo_children():
        empty.append(n)
ok("ни одна страница не пустая", not empty, str(empty))
ok("лог на месте и виден", app.logw.winfo_manager() != "")

# --- кнопка не должна подменять состояние Tkinter ---
# Раньше словарь состояния назывался self.state и затирал метод Tk,
# на котором держится про заголовок окна. customtkinter падал сразу.
ok("self.state не затирает метод Tk", not isinstance(app.state, dict),
   type(app.state).__name__)
ok("состояние запуска отдельно", isinstance(app.run_state, dict))

# --- цепочки шагов те же, что до переезда ---
ok("дайджест: кнопка отправила шаги",
   fill_and_press("Дайджест", "Собрать рыбу", [FAKE_DIR]) and bool(captured))
d = [t for t, _ in captured[-1]]
ok("дайджест: шаги по порядку",
   d == ["1. Определяю планы", "2. Раскладываю по склейкам",
         "3. XML для Premiere"], str(d))
argv = dict(captured[-1])["2. Раскладываю по склейкам"]
ok("дайджест: режим digest на месте", "--mode" in argv
   and "digest" in argv, str(argv[:6]))

fill_and_press("Интервью 1 видео", "Собрать рыбу", [str(FAKE)])
one = [t for t, _ in captured[-1]]
ok("одно видео: шаги по порядку",
   one == ["1. Рыба по тексту", "2. XML для Premiere", "3. Субтитры"],
   str(one))

# без файлов кнопка обязана просить, а не падать и не вешать тест
before = len(captured)
fill_and_press("Интервью 1 видео", "Собрать рыбу")
ok("без исходника шаги не запускаются", len(captured) == before)
ok("и пользователю сказано, чего не хватает", bool(warned), str(warned))

# --- вертикаль 9:16 ---
# Блок собирает полный цикл и отдаёт Premiere ДРУГОЕ окно времени:
# вертикальный файл уже нарезан, поэтому in/out у него с нуля.
# Ошибка в порядке шагов здесь стоила бы неверного монтажа, а не
# сообщения об ошибке, поэтому цепочка проверяется жёстко.
ok("вертикаль: кнопка отправила шаги",
   fill_and_press("Вертикаль 9:16", "Собрать вертикально",
                  [str(FAKE), str(FAKE), str(FAKE), str(FAKE), str(FAKE_DIR)])
   and bool(captured))
vert = [t for t, _ in captured[-1]]
ok("вертикаль: шаги по порядку",
   vert == ["1. Синхронизация", "2. Рыба по тексту", "3. Вертикаль 9:16",
            "4. XML для Premiere", "5. Субтитры"], str(vert))
vsteps = dict(captured[-1])
vpx = vsteps["3. Вертикаль 9:16"]
ok("вертикаль: перевод идёт отдельным шагом",
   vpx[0].endswith("vertical.py"), str(vpx[:1]))
ok("вертикаль: слежение за лицом включено по умолчанию",
   "--no-face" not in vpx, str(vpx))
vpre = vsteps["4. XML для Premiere"]
ok("вертикаль: Premiere получает вертикальный кадр",
   "--width" in vpre and "1080" in vpre and "--height" in vpre and "1920" in vpre,
   str(vpre))
ok("вертикаль: субтитры берутся из вертикальной раскладки",
   "vertical/selects.csv" in " ".join(vsteps["5. Субтитры"]),
   str(vsteps["5. Субтитры"]))
ok("вертикаль: второй ракурс отдельной дорожкой",
   "--stacked" in vpre, str(vpre))
ok("и в горизонтальном интервью переключателя вертикали больше нет",
   fill_and_press("Интервью 2 камеры", "Собрать рыбу",
                  [str(FAKE), str(FAKE), str(FAKE), str(FAKE), str(FAKE_DIR)])
   and not any("vertical.py" in str(a)
               for _t, a in captured[-1]), str([t for t, _ in captured[-1]]))

# --- новостной сюжет ---
# Логику раскладки проверяет test_news.py. Здесь - форма: разбор полей,
# валидация и цепочка шагов.
PN = A.pages_news

ok("титры «Имя — Должность» разбираются",
   PN.parse_titles("Иван — журналист\nПётр") ==
   [{"name": "Иван", "role": "журналист"}, {"name": "Пётр", "role": ""}],
   str(PN.parse_titles("Иван — журналист\nПётр")))
ok("титры переживают пустые строки",
   PN.parse_titles("\n  \nА — Б\n\n") == [{"name": "А", "role": "Б"}],
   str(PN.parse_titles("\n  \nА — Б\n\n")))
ok("перебивки берутся из папки",
   [_P(p).name for p in PN.scan_broll(FAKE_DIR)] == ["interview.mp4"],
   str(PN.scan_broll(FAKE_DIR)))
ok("перебивки из чужой папки не роняют программу",
   PN.scan_broll("нет/такой/папки") == [])

# строка спикера добавляется и удаляется
app.show("Новостной сюжет")
app.update_idletasks()
page = app.host.winfo_children()[0]
add_btn = [w for w in all_widgets(page)
           if isinstance(w, A.ctk.CTkButton) and w.cget("text") == "+ Ещё спикер"]
ok("кнопка добавления спикера есть", bool(add_btn))
n0 = len([w for w in all_widgets(page) if isinstance(w, PN.SyncRow)])
if add_btn:
    add_btn[0].invoke()
    app.update_idletasks()
n1 = len([w for w in all_widgets(page) if isinstance(w, PN.SyncRow)])
ok("спикер добавляется", n1 == n0 + 1, f"{n0} -> {n1}")

# без единого синхрона кнопка обязана объяснить и не запустить шаги
before = len(captured)
warned.clear()
fill_and_press("Новостной сюжет", "Собрать сюжет")
ok("пустой сюжет не запускает сборку", len(captured) == before)
ok("и объясняет, чего не хватает", bool(warned), str(warned))

# полная сборка. Порядок полей - как на странице: сначала «Куда
# сложить», потом подготовка (4 поля синхронизации), потом строка
# спикера (имя, должность, видео, микрофон, транскрипт), дальше
# перебивки, закадр и стендап. Подготовку оставляем пустой: собранный
# проект уже есть, синхронизация не нужна.
tx = FAKE.parent / "transcript.txt"
tx.write_text("Ну вот в 2024 году произошёл рекорд и скандал",
              encoding="utf-8")
NEWS_VALS = [FAKE_DIR, "", "", "", "",
             str(FAKE), "продюсер", str(FAKE), "", str(tx),
             "", "", "", "", ""]
fill_and_press("Новостной сюжет", "Собрать сюжет", list(NEWS_VALS))
news_steps = [t for t, _ in captured[-1]]
ok("сюжет: шаги по порядку",
   news_steps == ["1. Раскладка сюжета", "2. XML для Premiere", "3. Субтитры"],
   str(news_steps))
news_argv = dict(captured[-1])["1. Раскладка сюжета"]
ok("сюжет: spec и outdir передаются",
   "--spec" in news_argv and "--outdir" in news_argv, str(news_argv))
prem_argv = dict(captured[-1])["2. XML для Premiere"]
ok("сюжет: premiere получает selects.csv сюжета",
   prem_argv[prem_argv.index("--timeline") + 1].replace("\\", "/")
   .endswith("rz/selects.csv"), str(prem_argv))
# главное: spec.json должен существовать и быть валидным, иначе шаг 1
# упадёт уже после того, как пользователь отсидит всю сборку
spec_path = _P(FAKE_DIR) / "spec.json"
ok("spec.json создан", spec_path.is_file(), str(spec_path))
if spec_path.is_file():
    import json as _json
    spec = _json.loads(spec_path.read_text(encoding="utf-8"))
    ok("в spec попал синхрон с видео",
       spec.get("syncs") and spec["syncs"][0].get("media") == str(FAKE),
       str(spec.get("syncs"))[:200])
    ok("у синхрона есть транскрипт",
       bool((spec.get("syncs") or [{}])[0].get("transcript")))
    ok("пустые строки спикера не попали в spec",
       len(spec.get("syncs", [])) == 1, str(len(spec.get("syncs", []))))
    ok("без перебивок поле broll не добавляется", "broll" not in spec,
       str(spec.get("broll")))
    ok("без закадра поле vo не добавляется", "vo" not in spec)
    ok("без стендапа поле standup не добавляется", "standup" not in spec)

# та же сборка, но перебивки указаны - поле обязано появиться в spec
_broll_vals = list(NEWS_VALS)
_broll_vals[10] = FAKE_DIR          # папка с перебивками
fill_and_press("Новостной сюжет", "Собрать сюжет", _broll_vals)
spec2 = _json.loads(spec_path.read_text(encoding="utf-8"))
ok("перебивки попадают в spec",
   [_P(p).name for p in spec2.get("broll", [])] == ["interview.mp4"],
   str(spec2.get("broll")))

# синхрон без транскрипта - лид не выбрать, это надо сказать заранее
warned.clear()
before = len(captured)
_no_tx = list(NEWS_VALS)
_no_tx[9] = ""                    # транскрипт пустой
fill_and_press("Новостной сюжет", "Собрать сюжет", _no_tx)
ok("синхрон без транскрипта не запускает сборку", len(captured) == before)
ok("и предупреждение про транскрипт есть",
   any("транскрипт" in w.lower() for w in warned), str(warned))

# пустая папка - не ошибка, а понятное сообщение
_empty = _P(tempfile.mkdtemp())
app.show("Интервью 2 камеры")
app.update_idletasks()
panel = app.panels[0]
panel.dir_var.set(str(_empty))
ok("в пустой папке панель не падает", panel.load() is False)
ok("и говорит, что раскладки нет", "раскладки" in panel.info.cget("text"),
   panel.info.cget("text"))

# --- блок самодостаточен: свои шаги внутри, без «сходи в другой блок» ---
app.show("Новостной сюжет")
app.update_idletasks()
_page = app.host.winfo_children()[0]
_joined = " ".join(
    w.cget("text") for w in all_widgets(_page)
    if isinstance(w, A.ctk.CTkLabel) and w.cget("text"))
_buttons = [w.cget("text") for w in all_widgets(_page)
            if isinstance(w, A.ctk.CTkButton)]
ok("сюжет никого не отправляет в другой блок",
   "Интервью 2 камер" not in _joined, _joined[:200])
ok("у сюжета своя синхронизация", "Синхронизировать" in _buttons,
   str(_buttons))
ok("у сюжета своё распознавание речи", "Распознать речь" in _buttons,
   str(_buttons))
ok("у сюжета есть автозаполнение полей", "Подставить" in _buttons,
   str(_buttons))

# --- ни один блок не отправляет в другой ---
# Правило владельца: блок самодостаточен, работать надо в том, который
# нужен. Текст «сначала прогони другой блок» в интерфейсе - брак.
# «Мультикам» тут нет: это уже не блок, а карточка внутри интервью,
# и упоминать её название внутри страницы - нормально.
OTHERS = ["Интервью 2 камеры", "Новостной сюжет", "Дайджест",
          "Интервью 1 видео", "Проверка готового"]
for _name in [n for n, _ in A.TPL if n not in ("Настройки", "Ход работы")]:
    app.show(_name)
    app.update_idletasks()
    _pg = app.host.winfo_children()[0]
    _txt = " ".join(
        w.cget("text") for w in all_widgets(_pg)
        if isinstance(w, (A.ctk.CTkLabel, A.ctk.CTkButton))
        and w.cget("text") and isinstance(w.cget("text"), str))
    _bad = [o for o in OTHERS if o != _name and o in _txt]
    ok(f"«{_name}» не отправляет в другой блок", not _bad, str(_bad))

# блоки, которым нужна синхронизация, должны уметь сделать её сами.
# У интервью синхронизация идёт первым шагом «Собрать рыбы»,
# у сюжета - своей кнопкой.
for _name in ("Новостной сюжет",):
    app.show(_name)
    app.update_idletasks()
    _btns = [w.cget("text") for w in all_widgets(app.host.winfo_children()[0])
             if isinstance(w, A.ctk.CTkButton)]
    ok(f"«{_name}» умеет синхронизировать сам",
       "Синхронизировать" in _btns, str(_btns))

# «Интервью 1 видео»: поле перебивок было мёртвым - объявлено, но в
# интервью не передавалось. Теперь подключено и есть кнопка, делающая CSV.
app.show("Интервью 1 видео")
app.update_idletasks()
_btns1 = [w.cget("text") for w in all_widgets(app.host.winfo_children()[0])
          if isinstance(w, A.ctk.CTkButton)]
ok("одна камера умеет искать перебивки сама",
   "Найти перебивки" in _btns1, str(_btns1))

_bcsv = _P(tempfile.mkdtemp()) / "broll.csv"
_bcsv.write_text("file,plan,hero,duration,desc,conf,path\n"
                 "b1.mp4,крупный,,-,перебивка,0.9,%s\n"
                 % (_bcsv.parent / "b1.mp4"), encoding="utf-8")
_ONE_DIR = str(_P(FAKE).parent)

fill_and_press("Интервью 1 видео", "Собрать рыбу",
               [str(FAKE), _ONE_DIR, "", str(_bcsv)])
ok("одна камера: шаги по порядку",
   [t for t, _ in captured[-1]] == ["1. Рыба по тексту", "2. XML для Premiere",
                                    "3. Субтитры"], str([t for t, _ in captured[-1]]))
argv1 = dict(captured[-1])["1. Рыба по тексту"]
ok("CSV перебивок реально передаётся в интервью",
   "--broll" in argv1 and str(_bcsv) in argv1, str(argv1))

fill_and_press("Интервью 1 видео", "Собрать рыбу",
               [str(FAKE), _ONE_DIR, "", ""])
argv2 = dict(captured[-1])["1. Рыба по тексту"]
ok("без перебивок шаг тоже собирается", "--broll" not in argv2, str(argv2))

# --- разбор монтажа встроен в блоки сборки ---
# Отдельных страниц «Таймлайн» и «Просмотр» больше нет: разбор должен
# лежать в том же блоке, который собрал монтаж.
PR = A.panel_result
ok("отдельной страницы «Таймлайн» не осталось",
   not any(n == "Таймлайн" for n, _ in A.TPL))
ok("отдельной страницы «Просмотр» не осталось",
   not any(n == "Просмотр" for n, _ in A.TPL))
# мультикам - второй способ разложить две камеры, отдельный пункт не нужен
ok("отдельного блока «Мультикам» не осталось",
   not any(n == "Мультикам" for n, _ in A.TPL))

app.show("Интервью 2 камеры")
app.update_idletasks()
_mc_txt = " ".join(
    w.cget("text") for w in all_widgets(app.host.winfo_children()[0])
    if isinstance(w, (A.ctk.CTkLabel, A.ctk.CTkButton))
    and w.cget("text") and isinstance(w.cget("text"), str))
_mc_btns = [w.cget("text") for w in all_widgets(app.host.winfo_children()[0])
            if isinstance(w, A.ctk.CTkButton)]
ok("мультикам живёт в двухкамерном интервью",
   "Мультикам в Premiere" in _mc_txt, _mc_txt[:120])
ok("внутри есть подготовка камер",
   "Подготовить камеры" in _mc_btns, str(_mc_btns))
ok("и папка synced подставляется сама", "Подставить" in _mc_btns,
   str(_mc_btns))
# в одно-камерном интервью мультикаму нечего готовить
app.show("Интервью 1 видео")
app.update_idletasks()
_one_txt = " ".join(
    w.cget("text") for w in all_widgets(app.host.winfo_children()[0])
    if isinstance(w, (A.ctk.CTkLabel, A.ctk.CTkButton))
    and w.cget("text") and isinstance(w.cget("text"), str))
ok("в одной камере мультикама нет",
   "Мультикам в Premiere" not in _one_txt, _one_txt[:120])

with_panel = []
for n, _ in A.TPL:
    app.show(n)
    app.update_idletasks()
    if app.panels:
        with_panel.append(n)
ok("разбор есть в блоках, которые что-то собирают",
   # «Вертикаль 9:16» тоже собирает монтаж, поэтому панель ему
   # положена по тому же правилу. Раньше его не было в списке, потому
   # что он был переключателем внутри интервью, а не отдельным блоком.
   set(with_panel) == {"Интервью 2 камеры", "Новостной сюжет", "Дайджест",
                       "Интервью 1 видео", "Вертикаль 9:16"},
   str(with_panel))

app.show("Настройки")
app.update_idletasks()
ok("в «Настройках» панели нет", not app.panels)

# панель ловит раскладку в папке проекта
_proj = _P(tempfile.mkdtemp())
(_proj / "rz").mkdir()
(_proj / "rz" / "selects.csv").write_text(
    "file,duration,in,out,track,hero,path,alt,alt_path,audio\n"
    "a.mp4,10,0,10,V1,Ведущий,%s,b.mp4,%s,m1.wav\n"
    "b.mp4,5,0,5,V1,Гость,%s,,,m2.wav\n"
    % (_proj / "a.mp4", _proj / "b.mp4", _proj / "b.mp4"),
    encoding="utf-8")
app.show("Интервью 2 камеры")
app.update_idletasks()
panel = app.panels[0]
panel.dir_var.set(str(_proj))
ok("панель прочитала раскладку", panel.load() is True)
ok("панель увидела оба куска", len(panel.rows) == 2, str(len(panel.rows)))
ok("нашлась дорожка второго ракурса",
   any(l["name"] == "V2" for l in PR.TL.build_timeline(panel.rows)["lanes"]))
ok("панель показывает хронометраж", "15" in panel.info.cget("text").replace(
    ":", ""), panel.info.cget("text"))# раскладку правят руками, часто в Excel - лишняя запятая не должна
# ронять ни панель, ни экспорт
_broken = _P(tempfile.mkdtemp())
(_broken / "selects.csv").write_text(
    "file,duration,in,out,track,path\n"
    "a.mp4,10,0,10,V1,%s,,,лишнее\n" % (_broken / "a.mp4"),
    encoding="utf-8")
_br, _bf = PR.TL.read_rows(_broken / "selects.csv")
ok("лишние колонки в CSV не роняют чтение", len(_br) == 1, str(len(_br)))
ok("и не попадают в колонки", all(k != "__extra__" for k in _br[0]),
   str(list(_br[0])))

# правка через панель: выкинуть кусок и пересобрать
panel.dir_var.set(str(_proj))
panel.load()
panel._set_sel(0)
ok("после выбора активна кнопка удаления",
   panel.b_drop.cget("state") == "normal")
panel.drop()
ok("после удаления кусков на одну меньше", len(panel.rows) == 1,
   str(len(panel.rows)))
ok("панель помечает несохранённые правки", panel.dirty is True)
panel._set_sel(None)
ok("кнопка удаления снова неактивна",
   panel.b_drop.cget("state") == "disabled")

# --- чистая логика дорожек ---
TL = PR.TL


def _row(**kw):
    base = {"file": "a.mp4", "duration": 10.0, "in": 0.0, "out": 10.0,
            "path": "", "audio": "", "alt": "", "alt_path": "",
            "track": "V1", "hero": "", "desc": "", "plan": ""}
    base.update(kw)
    return base


# три куска: первый с микрофоном, второй с вторым ракурсом, третий закадр
tl_rows = [
    _row(file="a.mp4", audio="m1.wav", alt="b.mp4", alt_path="b.mp4"),
    _row(file="b.mp4", audio="m2.wav", duration=5.0),
    _row(file="vo.wav", track="A2", audio="", duration=3.0),
]
tl = TL.build_timeline(tl_rows)
tl_names = [l["name"] for l in tl["lanes"]]
ok("хронометраж сходится с суммой кусков",
   abs(tl["total"] - 18.0) < 1e-6, str(tl["total"]))
ok("видео выше звука",
   all(l["kind"] in ("video", "alt") for l in tl["lanes"][:2]),
   str(tl_names))
ok("второй ракурс завёл отдельную дорожку", "V2" in tl_names, str(tl_names))
ok("оба микрофона на своих дорожках",
   sum(1 for n in tl_names if n.startswith("♪")) == 3, str(tl_names))
# куски идут подряд, без зазоров - так же их складывает premiere.py
v1 = next(l for l in tl["lanes"] if l["name"] == "V1")
ok("куски на V1 стоят подряд",
   [round(i["start"], 3) for i in v1["items"]] == [0.0, 10.0],
   str([(i["start"], i["dur"]) for i in v1["items"]]))
ok("закадр без картинки не попал на видеодорожку",
   all(i["row"] != 2 for i in v1["items"]),
   str([i["row"] for i in v1["items"]]))
ok("микрофон первого куска тянется на его длину",
   round(v1["items"][0]["dur"], 3) == 10.0, str(v1["items"][0]["dur"]))
ok("закадр лёг на самостоятельную дорожку",
   any(l["kind"] == "audio" and any(i["role"] == "own" for i in l["items"])
       for l in tl["lanes"]), str(tl_names))

# пустой кусок не должен исчезнуть с дорожки
thin = TL.build_timeline([_row(duration=0.0)])
ok("нулевой кусок всё равно виден и кликабелен",
   thin["lanes"] and thin["lanes"][0]["items"][0]["dur"] > 0,
   str(thin["lanes"]))

# --- смена ракурса ---
sw = _row(file="a.mp4", path="a.mp4", alt="b.mp4", alt_path="b.mp4")
ok("ракурсы меняются местами", TL.swap_angle(sw) is True)
ok("после обмена верхним стал второй ракурс",
   sw["file"] == "b.mp4" and sw["alt"] == "a.mp4", str(sw))
ok("пути тоже поменялись",
   sw["path"] == "b.mp4" and sw["alt_path"] == "a.mp4", str(sw))
nosw = _row(file="a.mp4", alt="", alt_path="")
ok("без второго ракурса обмен отказывает", TL.swap_angle(nosw) is False)

# --- сохранение с копией ---
_save_dir = _P(tempfile.mkdtemp())
_scsv = _save_dir / "selects.csv"
_scsv.write_text("file,duration,track,hero\na.mp4,10,V1,ведущий\n"
                 "b.mp4,5,V2,гость\n", encoding="utf-8")
_rows, _fields = TL.read_rows(_scsv)
ok("прочитаны обе строки", len(_rows) == 2, str(len(_rows)))
ok("числа стали числами", _rows[1]["duration"] == 5.0, str(_rows[1]))
_rows.pop(0)
TL.write_rows(_scsv, _rows, _fields)
ok("исходный файл отдан под копию", _scsv.with_suffix(".csv.bak").is_file())
_back, _bf = TL.read_rows(_scsv.with_suffix(".csv.bak"))
ok("в копии остались обе строки", len(_back) == 2, str(len(_back)))
_after, _af = TL.read_rows(_scsv)
ok("в новом файле куска на одну меньше", len(_after) == 1, str(len(_after)))
ok("колонки не потерялись при записи",
   _af == ["file", "duration", "track", "hero"], str(_af))
ok("колонки пережили редактирование без перестановок",
   _after[0]["hero"] == "гость", str(_after[0]))

app.destroy()
print()
if fail:
    print(f"UI FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("UI OK")

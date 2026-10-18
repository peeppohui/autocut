# -*- coding: utf-8 -*-
"""Плашки: ФИО и должность человека, которого берут интервью.

Стандарт медиацентра требует плашку на корреспондента и на синхрон,
не короче пяти секунд, и написание ФИО как «Имя Фамилия», а должность
отдельной строкой ниже.

Чего этот модуль НЕ делает и почему. Плашка в Premiere - это Motion
Graphics Template из Local Templates (`SVS_low_third`), а такую ссылку
формат FCP7 не умеет: в нём нет ни понятия шаблона, ни его полей.
Попытка вписать это руками кончается так же, как `<multiclip>`: Premiere
26 импортирует молча и роняет файл целиком. Поэтому здесь ровно три
вещи, которые формат позволяет:

  1. PNG плашки в стиле стандарта - его показывают тому, кто согласует;
  2. список в текстовом файле с таймкодами - что кому и куда вешать;
  3. маркеры в секвенции на месте каждой плашки.

Сам шаблон `SVS_low_third` в Premiere ставит монтажёр: AutoCut только
готовит текст, таймкод и картинку для согласования.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import settings  # noqa: E402

# ---- стиль плашки, снятый с примера из стандарта -------------------------

BAR_LEFT = 0.185        # планка начинается примерно с этой доли ширины
BAR_RIGHT = 0.815
BAR_BOTTOM = 0.115      # низ планки над нижним краем кадра
BAR_HEIGHT = 0.175      # высота планки: два шрифта плюс воздух
BAR_ALPHA = 208
BLUE = (36, 52, 148)
VIOLET = (92, 58, 152)
WHITE = (255, 255, 255)
BRACKET_W = 0.0035      # толщина угловых скобок, в долях ширины кадра
BRACKET_ARM = 0.032     # длина плеча скобки, в долях ширины кадра
BRACKET_DOWN = 0.46     # на сколько высоты планки уходит скобка вниз
SQUARE_SIDE = 0.0085    # сторона декоративного квадрата, в долях ширины
SIDE_MARGIN = 0.115     # поля под декорацию слева и справа, в долях планки
NAME_MAX = 0.058        # наибольший кегль ФИО, в долях высоты кадра
POS_MAX = 0.029

# шрифт стандарта — геометрический гротеск с разрядкой. Montserrat на
# рабочих машинах медиацентра не стоит, поэтому под неё есть запас.
FONT_CANDIDATES = ["montserratregular", "montserrat-regular", "bahnschrift",
                   "segoeui", "arial"]


def find_font(bold: bool = False) -> Optional[str]:
    """Путь к шрифту. Разрядку рисуем сами, поэтому нужен только гротеск."""
    d = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    names = FONT_CANDIDATES
    if bold:
        names = [n + "bd" if n.startswith("segoeui") else n for n in names]
        names = ["montserratbold", "montserrat-bold", "segoeuib",
                 "arialbd", "bahnschrift"] + names
    for stem in names:
        for ext in (".ttf", ".otf"):
            p = d / (stem + ext)
            if p.is_file():
                return str(p)
    return None


def _tracked(draw, xy, text: str, font, fill, tracking: float):
    """Текст с разрядкой. PIL её не умеет, а в стандарте она видна везде."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return x


def _tracked_width(draw, text: str, font, tracking: float) -> float:
    if not text:
        return 0.0
    return sum(draw.textlength(c, font=font) for c in text) \
        + tracking * (len(text) - 1)


def render(name: str, position: str, out_png: Path,
           width: int = 1920, height: int = 1080,
           tracking: float = 0.0) -> bool:
    """Рисует плашку в PNG с прозрачностью. True — нарисовали."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("Нет PIL - плашку не нарисовать. pip install pillow")
        return False

    fp = find_font(True)
    if not fp:
        print("Не нашёлся шрифт для плашки.")
        return False
    if tracking <= 0:
        tracking = round(height * 0.006)

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    bh = round(height * BAR_HEIGHT)
    by = round(height * (1 - BAR_BOTTOM)) - bh
    bx0 = round(width * BAR_LEFT)
    bx1 = round(width * BAR_RIGHT)

    # Планка: горизонтальный градиент синий -> фиолетовый, полупрозрачный.
    grad = Image.new("RGBA", (bx1 - bx0, bh))
    gd = ImageDraw.Draw(grad)
    span = max(1, bx1 - bx0 - 1)
    for x in range(span):
        t = x / span
        col = tuple(round(BLUE[i] + (VIOLET[i] - BLUE[i]) * t) for i in range(3))
        gd.line([(x, 0), (x, bh)], fill=col + (BAR_ALPHA,))
    img.alpha_composite(grad, (bx0, by))
    d = ImageDraw.Draw(img)

    # Угловые скобки — по одной на каждом углу, тонкие и длинные.
    bt = max(2, round(width * BRACKET_W))
    arm = round(width * BRACKET_ARM)
    down = round(bh * BRACKET_DOWN)
    for cx, cy, sx, sy in ((bx0, by, 1, 1), (bx1, by, -1, 1),
                           (bx0, by + bh, 1, -1), (bx1, by + bh, -1, -1)):
        d.line([(cx, cy), (cx + sx * arm, cy)], fill=WHITE + (255,), width=bt)
        d.line([(cx, cy), (cx, cy + sy * down)], fill=WHITE + (255,), width=bt)

    # Декоративные квадраты: ряд слева внизу и сетка справа.
    sq = round(width * SQUARE_SIDE)
    gap = round(sq * 0.6)
    sy = by + bh - sq - round(bh * 0.10)
    for i in range(4):
        x0 = bx0 + round((bx1 - bx0) * 0.035) + i * (sq + gap)
        d.rectangle([x0, sy, x0 + sq, sy + sq], fill=WHITE + (215,))
    gx = bx1 - round((bx1 - bx0) * 0.035) - sq
    gy = by + round(bh * 0.14)
    for r in range(3):
        for c in range(3):
            x0 = gx - c * (sq + gap)
            y0 = gy + r * (sq + gap)
            d.rectangle([x0, y0, x0 + sq, y0 + sq], fill=WHITE + (235,))

    # Текст. Должность бывает длиннее планки («студентка направления
    # телевидение СГТУ имени Гагарина»), поэтому кегль подбирается под
    # ширину: текст должен поместиться между декрациями, а не вылезать
    # за планку, как вышло с первого раза.
    zone = round((bx1 - bx0) * (1 - 2 * SIDE_MARGIN))
    name_path = find_font(True)
    pos_path = find_font(False) or name_path

    def fit(text, path, max_frac, track_px):
        """Наибольший кегль, при котором текст влезает в зону.

        max_frac — доля высоты кадра, track_px — разрядка при этом
        кегле. Кегль уменьшается целыми пикселями, а разрядка ведёт
        себя пропорционально ему: если бы она осталась прежней, текст
        на мелком кегле разъехался бы, а на крупном налезал.
        """
        top = round(height * max_frac)
        size = top
        while size > 10:
            f = ImageFont.truetype(path, size)
            probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
            if _tracked_width(probe, text, f, track_px * size / top) <= zone:
                return f, track_px * size / top
            size -= 1
        f = ImageFont.truetype(path, 10)
        return f, track_px * 10 / top

    trk_n = round(height * 0.006)
    f_name, trk_n = (fit(name, name_path, NAME_MAX, trk_n) if name else (None, 0))
    f_pos, trk_p = (fit(position, pos_path, POS_MAX, trk_n * 0.5)
                    if position else (None, 0))

    def center(text, font, trk, y):
        if not text or font is None:
            return
        w = _tracked_width(d, text, font, trk)
        _tracked(d, (round((width - w) / 2), round(y)), text, font,
                 WHITE + (255,), trk)

    name_px = f_name.size if f_name else 0
    pos_px = f_pos.size if f_pos else 0
    if name_px and pos_px:
        block = name_px + round(height * 0.012) + pos_px
        y = by + (bh - block) / 2
        center(name, f_name, trk_n, y)
        center(position, f_pos, trk_p, y + name_px + round(height * 0.012))
    elif name_px:
        center(name, f_name, trk_n, by + (bh - name_px) / 2)
    else:
        center(position, f_pos, trk_p, by + (bh - pos_px) / 2)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    # PIL на Windows спотыкается о кириллицу в пути, поэтому пишем байтами
    out_png.write_bytes(_png_bytes(img))
    return True


def _png_bytes(img) -> bytes:
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def people() -> Dict[str, Dict[str, str]]:
    """Кто снимается: ключ роли из раскладки -> ФИО и должность."""
    raw = settings.get("lower_thirds.people", {})
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for role, v in raw.items():
        if isinstance(v, dict):
            out[str(role).strip().lower()] = {
                "fio": str(v.get("fio", "")).strip(),
                "position": str(v.get("position", "")).strip(),
            }
    return out


def plan(clips: List[Dict], who: Dict[str, Dict[str, str]],
         min_sec: float = 5.0, fps: int = 25,
         every_appearance: bool = False) -> Tuple[List[Dict], List[str]]:
    """Где нужны плашки. Возвращает (плашки, пропуски с причинами).

    По стандарту плашка вешается на корреспондента и на синхрон, то есть
    на каждое появление человека в кадре. Но подряд идущие куски одного
    человека - это один синхрон, а не несколько: плашка на каждом куске
    получилась бы мельтешением. Поэтому плашка ставится на первое
    появление после чужого, и только если кусок тянется дольше
    `min_sec` - иначе её не успевают прочитать.

    Пропуски не молчат: если у кого-то первый кусок короче `min_sec`, об
    этом пишется строкой. Иначе монтажёр решит, что про человека
    забыли, а его просто не успели бы прочитать.

    `every_appearance=True` отключает склейку соседних и вешает плашку
    на каждое появление.
    """
    out: List[Dict] = []
    notes: List[str] = []
    t = 0.0
    prev_role: Optional[str] = None
    short: Dict[str, float] = {}
    for c in clips:
        # Длительность приходит двумя способами и их нельзя мешать:
        # `_used` ставит premiere.py и это КАДРЫ, а `duration` в раскладке
        # это СЕКУНДЫ. Делить на fps второе — типичная ошибка, из-за которой
        # кусок в десять секунд считался за 0.4с и плашки не появлялись.
        if c.get("_used"):
            dur = float(c["_used"]) / fps
        else:
            dur = float(c.get("duration") or 0)
        role = str(c.get("hero", "")).strip().lower()
        start, end = t, t + dur
        t = end
        p = who.get(role)
        if not p or not p.get("fio"):
            continue
        new_appearance = role != prev_role or every_appearance
        if new_appearance and dur < min_sec:
            # запомним самую длинную попытку — по ней и скажем причину
            short[role] = max(short.get(role, 0.0), dur)
            prev_role = role
            continue
        if new_appearance:
            out.append({"role": role, "fio": p["fio"],
                        "position": p.get("position", ""),
                        "start": start, "dur": dur})
        prev_role = role

    for role, d in short.items():
        if any(e["role"] == role for e in out):
            continue
        notes.append(f"{who[role]['fio']}: самое длинное появление {d:.1f}с, "
                     f"а плашка держится не меньше {min_sec:.0f}с — "
                     f"не успели бы прочитать, вешать нечего.")
    return out, notes


def timecode(sec: float, fps: int = 25) -> str:
    """Таймкод так же, как его показывает Premiere: ЧЧ:ММ:СС:КК.

    Формат минуты-секунды выглядит короче, но монтажёр сверяет список с
    панелью Premiere, где стоит часовой. Расхождение в записи заставит
    искать глазами, где плашка, вместо того чтобы поставить её сразу.
    """
    total = int(round(sec * fps))
    ff = total % fps
    total //= fps
    return f"{total // 3600:02d}:{total // 60 % 60:02d}:" \
           f"{total % 60:02d}:{ff:02d}"


def write_report(entries: List[Dict], out_txt: Path, template: str,
                 notes: Optional[List[str]] = None, fps: int = 25) -> None:
    """Список плашек с таймкодами: что согласовать и куда повесить."""
    lines = [f"ПЛАШКИ ({template})", "=" * 52, ""]
    for n, e in enumerate(entries, 1):
        lines.append(f"{n}. {e['fio']}")
        if e["position"]:
            lines.append(f"   должность: {e['position']}")
        lines.append(f"   таймкод:    {timecode(e['start'], fps)} "
                     f"(висит {e['dur']:.1f} с)")
        lines.append("")
    if not entries:
        lines.append("Плашек не нужно: в раскладке нет известных намённых "
                     "персон или ни один кусок не тянется на 5 с.")
    for n in (notes or []):
        lines.append(f"Пропущено: {n}")
    if notes:
        lines.append("")
    lines.append("")
    lines.append(f"Вешать шаблон {template} из Local Templates. "
                 f"ФИО писать «Имя Фамилия», должность — строкой ниже.")
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    out_txt.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def marker_text(e: Dict) -> str:
    t = e["fio"] if not e["position"] else f"{e['fio']} — {e['position']}"
    return f"ПЛАШКА: {t} ({e['dur']:.0f}с)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="AutoCut Плашки — ФИО и должность по раскладке")
    ap.add_argument("--timeline", required=True, help="CSV раскладки (selects.csv)")
    ap.add_argument("--outdir", default="", help="куда класть плашки и список")
    ap.add_argument("--fps", type=int,
                    default=int(settings.get("export.fps", 25)))
    ap.add_argument("--every-appearance", action="store_true",
                    help="плашка на каждое появление, а не на первое "
                         "после чужого")
    args = ap.parse_args(argv)

    tl = Path(args.timeline)
    if not tl.is_file():
        print(f"Не найден таймкод: {tl}")
        return 2
    outdir = Path(args.outdir) if args.outdir else tl.parent / "plashki"
    outdir.mkdir(parents=True, exist_ok=True)

    with tl.open(encoding="utf-8-sig", newline="") as f:
        clips = list(csv.DictReader(f))
    if not clips:
        print(f"В {tl} нет строк.")
        return 1

    who = people()
    named = {k: v for k, v in who.items() if v.get("fio")}
    if not named:
        print("Ни для кого не заполнено ФИО в настройках "
              "(lower_thirds.people) — плашки строить не из чего.")
        return 1

    entries, notes = plan(clips, named,
                          float(settings.get("lower_thirds.min_sec", 5.0)),
                          args.fps, args.every_appearance)
    template = str(settings.get("lower_thirds.template", "SVS_low_third"))
    seen: Dict[str, Dict] = {}
    for e in entries:
        if e["fio"] in seen:
            continue
        p = outdir / f"{e['fio']}.png"
        if render(e["fio"], e["position"], p):
            seen[e["fio"]] = e
            print(f"  Плашка: {p.name}")

    rep = outdir / "plashki.txt"
    write_report(entries, rep, template, notes, args.fps)
    for n in notes:
        print(f"  Пропущено: {n}")
    print(f"Список плашек: {rep} ({len(entries)} шт.)")
    print("Покажи картинку тому, кто согласует, и повесь шаблон "
          f"{template} по таймкодам из списка.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
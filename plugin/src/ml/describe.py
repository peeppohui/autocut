# -*- coding: utf-8 -*-
"""Что в кадре нарезки: описание словами.

**Почему не картинка.** CLIP (`openai/clip-vit-base-patch32`) даёт
сходство 0.58–0.74 между ЛЮБЫМИ двумя кадрами нарезки: материал с
одного мероприятия, и для модели все кадры почти одинаковы. Блоки,
которые выбрал редактор, визуально не кучны (внутри блока
+0.005…+0.072 к фону, два блока отрицательные). Значит по картинке
выбрать осмысленный кадр нельзя.

**Что работает.** Описание словами различает: под «дети в халатах
делают опыты за столом» находится именно такой кадр, а под «вручают
награду» — кадр с награждением. Модель Qwen2-VL-2B-Instruct отвечает
по-русски и описывает кадр точнее, чем CLIP различает.

**Стоимость.** 4.4 ГБ весов, fp16 влезает в 6 ГБ VRAM. Описание 110
клипов по двум кадрам — минуты. Кеш в `_work/desc`, повторный прогон
бесплатный.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SRC / "config") not in sys.path:
    sys.path.insert(0, str(SRC / "config"))

import bootstrap            # noqa: E402,F401


DEFAULT_MODEL = "Qwen/Qwen2-VL-2B-Instruct"

# Промпт без перечисления категорий: с перечислением модель начинает
# отвечать «В кадре видны люди, предметы и место» — это не описание, а
# отказ описывать. Такой ответ, кстати, сам по себе признак пустого
# кадра: описывать нечего.
PROMPT = ("Что происходит в этом кадре? Опиши своими словами, что видно "
          "и где это происходит.")

# Ответы такого вида не различают кадры: значит кадр пустой.
EMPTY_MARKERS = ("в кадре видны люди, предметы и место",
                 "люди, предметы и место",
                 "не могу описать",
                 "нет информации")


class Describer:
    """Модель зовётся один раз на прогон: веса тяжёлые."""

    def __init__(self, model_name: str = DEFAULT_MODEL, device: str = "auto"):
        self.model_name = model_name
        self.device = device
        self._model = None
        self._proc = None

    def _load(self):
        if self._model is not None:
            return
        import torch
        from transformers import AutoProcessor
        try:
            from transformers import AutoModelForImageTextToText as _VLM
        except ImportError:
            from transformers import AutoModelForVision2Seq as _VLM
        # Картинка маленькая: 360x360 — это ~200 зрительных токенов
        # вместо ~1000. Замер на живом кадре: 10.8 с на описание при
        # полном размере и 7.1 с при уменьшенном. Разница видна глазом,
        # а разбор кадра от неё не меняется.
        self._proc = AutoProcessor.from_pretrained(
            self.model_name, min_pixels=56 * 56, max_pixels=360 * 360)
        dev = "cuda" if (self.device in ("auto", "cuda")
                         and torch.cuda.is_available()) else "cpu"
        # fp16, а не fp32. Модель 2B в fp32 занимает 8.8 ГБ, а видеопамяти
        # здесь 6 ГБ — не влезает. На видеокарте fp16 медленнее, чем на
        # процессоре с fp32, но тут выигрыш в три раза.
        dtype = torch.float16 if dev == "cuda" else torch.float32
        last = None
        for attn in ("sdpa", None):
            kw = {"dtype": dtype}
            if attn:
                kw["attn_implementation"] = attn
            try:
                self._model = _VLM.from_pretrained(self.model_name,
                                                   **kw).to(dev).eval()
                break
            except Exception as e:       # sdpa может не собраться
                last = e
                self._model = None
        if self._model is None:
            raise RuntimeError("не загрузилась модель описаний: {}".format(last))
        self._dev = dev
        self._dtype = dtype

    def describe(self, image) -> str:
        import torch
        self._load()
        msgs = [{"role": "user",
                 "content": [{"type": "image"}, {"type": "text",
                                                 "text": PROMPT}]}]
        prompt = self._proc.apply_chat_template(msgs, add_generation_prompt=True)
        inputs = self._proc(text=prompt, images=[image], return_tensors="pt")
        inputs = {k: (v.to(self._dev, dtype=self._dtype)
                      if v.dtype.is_floating_point else v.to(self._dev))
                  for k, v in inputs.items()}
        with torch.no_grad():
            out = self._model.generate(**inputs, max_new_tokens=40,
                                       do_sample=False)
        txt = self._proc.batch_decode(
            out[:, inputs["input_ids"].shape[1]:],
            skip_special_tokens=True)[0].strip()
        return txt

    def release(self):
        """Отпустить видеопамять: после описаний считаем векторы."""
        import torch
        self._model = None
        self._proc = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def frame_at(path: Path, t: float, out: Path):
    """Кадр из файла на момент t."""
    if out.exists():
        out.unlink()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i",
                    str(path), "-frames:v", "1", "-vf", "scale=640:-2",
                    str(out)], capture_output=True)
    if not out.is_file() or out.stat().st_size == 0:
        return None
    from PIL import Image
    return Image.open(out).convert("RGB")


def describe_clip(describer: "Describer", video: Path, work: Path,
                   dur: float, name: str = ""):
    """Описание клипа: кадр из середины.

    Один кадр, а не два: замер — 10.8 с на кадр при полном размере
    картинки, и два кадра на клип превращают сотню клипов в сорок
    минут. Середина клипа — то место, где он обычно содержательнее
    краёв (начало и конец монтажёр вырезает).
    """
    cache = work / (video.stem + ".desc.json")
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("model") == describer.model_name:
                return data.get("desc", "")
        except Exception:
            pass
    tmp = work / "_frame.jpg"
    img = frame_at(video, max(0.1, dur * 0.4), tmp)
    desc = ""
    if img is not None:
        try:
            desc = describer.describe(img)
        except Exception as e:
            print("  {}: не описать ({})".format(video.name, str(e)[:70]))
    if tmp.exists():
        tmp.unlink()
    if any(m in desc.lower() for m in EMPTY_MARKERS):
        desc = ""
    try:
        cache.write_text(json.dumps({"model": describer.model_name,
                                     "desc": desc}, ensure_ascii=False),
                         encoding="utf-8")
    except Exception:
        pass
    return desc


def describe_pool(folder: Path, work: Path, model_name: str = DEFAULT_MODEL,
                  device: str = "auto", log=None, limit: int = 0):
    """Описания всех клипов папки: {имя файла: описание}.

    Кеш в подпапке `desc`: описания дорогие, повторный прогон блока
    должен быть дешёвым.
    """
    cache_dir = work / "desc"
    cache_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in folder.iterdir()
                   if p.suffix.lower() in (".mp4", ".mov", ".mxf", ".avi"))
    if limit:
        files = files[:limit]
    describer = Describer(model_name, device)
    out = {}
    for i, p in enumerate(files, 1):
        cache = cache_dir / (p.stem + ".desc.json")
        if cache.is_file():
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                if data.get("model") == model_name:
                    out[p.name] = data.get("desc", "")
                    continue
            except Exception:
                pass
        dur = _duration(p)
        desc = describe_clip(describer, p, cache_dir, dur)
        out[p.name] = desc
        if log and i % 10 == 0:
            log("  описано {}/{}".format(i, len(files)))
    describer.release()
    return out


def _duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0

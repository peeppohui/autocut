# -*- coding: utf-8 -*-
"""Ритм музыки: сетка, по которой монтаж кладётся под бит.

Зачем это: «резать под бит» — значит не «резать ровно», а чтобы точки
монтажа попадали в удары. Значит нужно знать, где удары и с какой
частотой.

Как это сделано без librosa (её в портативном окружении нет):

1. Считаем спектральный поток — насколько резко меняется энергия в
   каждом коротком окне. Это «карта ударов»: в месте удара она
   прыгает. Берём модуль разности: удар слышно и по фронту, и по
   спаду, поэтому первый удар трека тоже виден.
2. По этой карте оцениваем темп: автокорреляция ищет лаг, который
   повторяется в карте (удар → удар → удар).
3. Сами удары находим пиками карты и строим сетку ПО НИМ, а не по
   идеальной формуле: ровная сетка со временем накапливает ошибку —
   за минуту монтаж уезжает на секунды.
4. Доли сетки — это удары и их середины: монтажная точка может лечь
   на удар или на половину удара.

Если удары нерегулярные (шум, а не музыка) — сетки нет: монтаж без
ритма лучше, чем монтаж, натянутый на случайные точки.
"""
from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np

RATE = 22050          # частота разбора для анализа
HOP = 512             # шаг окна в сэмплах -> ~43 кадра/с
WIN = 1024            # длина окна в сэмплах
MAX_SEC = 180.0       # дольше не смотрим: ритм в треке не меняется
MIN_BPM = 70.0        # 70..170 — обычный темп музыки
MAX_BPM = 170.0


def read_mono(path: Path, rate: int = RATE) -> np.ndarray:
    """Трек в моно float32.

    WAV читаем сами (в тестах ffmpeg может быть не в PATH), всё
    остальное — через ffmpeg в сырой s16le на stdout.
    """
    path = Path(path)
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as wf:
                raw = wf.readframes(wf.getnframes())
                width = wf.getsampwidth()
                if width == 2:
                    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                elif width == 1:
                    x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
                else:
                    x = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
                if wf.getnchannels() > 1:
                    x = x.reshape(-1, wf.getnchannels()).mean(axis=1)
                return x
        except Exception:
            return np.zeros(0, dtype=np.float32)
    try:
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le",
             "-ac", "1", "-ar", str(rate), "-"],
            capture_output=True, check=True).stdout
    except Exception:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768.0


def onset_envelope(x: np.ndarray, rate: int = RATE):
    """Карта ударов и её частота в кадрах в секунду."""
    if x.size < WIN * 4:
        return np.zeros(0, dtype=np.float32), rate / float(HOP)
    x = x[: int(min(x.size, MAX_SEC * rate))]
    n = 1 + (x.size - WIN) // HOP
    idx = np.arange(WIN)[None, :] + HOP * np.arange(n)[:, None]
    frames = x[idx]
    window = np.hanning(WIN).astype(np.float32)
    spec = np.abs(np.fft.rfft(frames * window, axis=1))
    # Модуль разности: удар виден и по фронту, и по спаду. Так первый
    # удар трека не теряется — при знаковой разности его не видно.
    flux = np.abs(np.diff(spec, axis=0)).sum(axis=1)
    if flux.size == 0:
        return np.zeros(0, dtype=np.float32), rate / float(HOP)
    top = float(np.percentile(flux, 99))
    if top <= 1e-9:
        return np.zeros(0, dtype=np.float32), rate / float(HOP)
    env = np.clip(flux / top, 0.0, 1.0).astype(np.float32)
    k = np.array([0.25, 0.5, 0.25], dtype=np.float32)
    env = np.convolve(env, k, mode="same")
    return env, rate / float(HOP)


def energy_map(x: np.ndarray, rate: int = RATE):
    """Энергия в окнах: где в сигнале звук, а где тишина.

    Нужна рядом с картой ударов: разность спектра хорошо показывает
    темп, но вершина у неё приходится на спад удара, а не на сам
    удар. По энергии удар виден там, где он и звучит — поэтому
    положение сетки берём отсюда, а ритм — из карты ударов.
    """
    if x.size < WIN * 4:
        return np.zeros(0, dtype=np.float32), rate / float(HOP)
    x = x[: int(min(x.size, MAX_SEC * rate))]
    n = 1 + (x.size - WIN) // HOP
    idx = np.arange(WIN)[None, :] + HOP * np.arange(n)[:, None]
    frames = x[idx]
    e = np.sqrt(np.mean(np.square(frames.astype(np.float64)), axis=1))
    top = float(np.percentile(e, 99))
    if top <= 1e-9:
        return np.zeros(0, dtype=np.float32), rate / float(HOP)
    return np.clip(e / top, 0.0, 1.0).astype(np.float32), rate / float(HOP)


def low_band(x: np.ndarray, rate: int = RATE, cutoff: float = 200.0):
    """Трек без верхов — «карта барабана».

    Удар слышен внизу: барабан, бочка, бас. Если считать удары по
    всему спектру, в сетку попадает каждая доля (струна, шум
    клавиш), и монтаж липнет к случайным точкам.
    """
    if x.size < 16:
        return np.zeros(0, dtype=np.float32)
    n = x.size
    spec = np.fft.rfft(x)
    freqs = np.arange(spec.size) * (float(rate) / float(n))
    spec = np.where(freqs <= cutoff, spec, 0.0)
    return np.fft.irfft(spec, n).astype(np.float32)


def rms(a: np.ndarray) -> float:
    """Громкость сигнала."""
    if a.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(a.astype(np.float64)))))


def beat_map(env: np.ndarray, env_low: np.ndarray, x: np.ndarray = None,
             x_low: np.ndarray = None):
    """Какую карту ударов брать: барабанную, если внизу есть чему быть.

    Возвращает (карта, использована_низкая_полоса).

    Сравниваем не «карту», а саму энергию: нормированная карта шума
    выглядит как удары (любой шум после нормировки заполняет весь
    диапазон), поэтому решает тихий ли низ.
    """
    if env_low.size != env.size:
        return env, False
    if x is not None and x_low is not None:
        full_r, low_r = rms(x), rms(x_low)
        if full_r <= 0.0 or low_r < 0.15 * full_r:
            return env, False
        return env_low, True
    if float(np.std(env_low)) < 0.2 * float(np.std(env)):
        return env, False
    return env_low, True


def estimate_lag(env: np.ndarray, fps: float, min_bpm: float = MIN_BPM,
                 max_bpm: float = MAX_BPM, count: int = 3):
    """Кандидаты лага удара (в кадрах): какие интервалы повторяются.

    Кандидатов несколько, потому что в карте ударов есть гармоники
    темпа: половина, удвоение. Дальше выбираем не самый громкий, а
    самый похожий на настоящий удар (см. pick_lag).
    """
    if env.size < 16:
        return []
    centred = env - env.mean()
    ac = np.correlate(centred, centred, mode="full")[env.size - 1:]
    if ac[0] <= 1e-9:
        return []
    ac = ac / ac[0]
    lo = max(2, int(round(fps * 60.0 / max_bpm)))
    hi = min(ac.size - 1, int(round(fps * 60.0 / min_bpm)))
    if hi <= lo:
        return []
    seg = ac[lo:hi + 1]
    # Локальные максимумы автокорреляции — это и есть варианты темпа.
    idx = [i for i in range(1, len(seg) - 1)
           if seg[i] >= seg[i - 1] and seg[i] >= seg[i + 1]]
    idx.sort(key=lambda i: -float(seg[i]))
    return [lo + i for i in idx[:count]]


def score_lag(env: np.ndarray, lag: int, fps: float, energy: np.ndarray = None):
    """Насколько лаг похож на настоящий удар: (оценка, регулярность).

    Считаем удары с расстоянием РОВНО lag (удары не могут быть чаще
    удара) и смотрим, какие они сильные и насколько ровные. Удар
    барабана — это пик, у доли — рябь.
    """
    peaks, regular, beatiness = find_beats(env, lag, energy)
    if peaks.size < 4:
        return 0.0, 0.0, 0.0
    strength = float(np.mean(env[peaks]))
    hot = float(np.mean(env[peaks] > 0.6))
    return regular * hot * strength * beatiness, regular, beatiness


def pick_lag(env: np.ndarray, env_low: np.ndarray, fps: float,
             energy: np.ndarray = None):
    """Лаг удара и карта, по которой удары видны лучше всего.

    Возвращает (lag, карта). Пробуем обе карты — полный спектр и
    низовую полосу: у настоящего барабана низ даёт настоящие удары,
    а у короткого щелчка (тест) низ бесполезен, там лучше полный
    спектр. Решает не «что тише», а где удары ровнее и сильнее.
    """
    cands = estimate_lag(env, fps)
    if not cands:
        return 0, env
    best = (0, 0.0, env)
    for map_env in (env, env_low):
        if map_env.size != env.size:
            continue
        for lag in cands:
            score, _, _ = score_lag(map_env, lag, fps, energy)
            if score > best[1]:
                best = (lag, score, map_env)
    return best[0], best[2]


def _grid_fit(peaks, period: float) -> float:
    """Доля ударов, попавших на ровную сетку с шагом period.

    Так мерить регулярность честнее, чем сравнивать промежутки между
    соседними ударами: в живой музыке исполнитель тянет и ускоряет
    удары, промежутки «гуляют» (0.58–0.98 с при медиане 0.79), и
    сравнение соседей даёт низкую оценку у хорошего трека. Сетка же
    остаётся ровной: попадание в неё и есть признак ритма.
    """
    if peaks.size < 4 or period <= 0:
        return 0.0
    phase = float(peaks[0]) % period
    tol = 0.3 * period
    hits = 0
    for p in peaks:
        k = round((float(p) - phase) / period)
        if abs(float(p) - (phase + k * period)) <= tol:
            hits += 1
    return hits / float(peaks.size)


def find_beats(env: np.ndarray, lag: int, energy: np.ndarray = None):
    """Удары (в кадрах) и оценка регулярности.

    Удары ищем трекингом: от самого сильного пика идём дальше и
    берём максимум в окне «один лаг ±». Обычный пик-пикинг here
    давал удары через раз (карта звенит на спаде щелчка, и половина
    ударов терялась) — трекинг берёт сильнейший пик каждого окна,
    то есть удар.

    Если дана карта энергии, вершину окна ищем по ней: удар звучит
    в момент щелчка, а карта разности спектра вершину сдвигает на
    спад. Темп (лаг) считаем по карте ударов, положение — по энергии.

    Регулярность — доля промежутков между ударами, близких к
    среднему. У настоящей музыки она высокая, у шума низкая: по
    ней мы и отличаем музыку от шума.
    """
    if lag <= 0 or env.size < 8:
        return np.zeros(0, dtype=int), 0.0, 0.0
    n = env.size
    gap_lo = max(1, int(lag * 0.6))
    gap_hi = max(gap_lo + 1, int(lag * 1.6))
    top = float(np.percentile(env, 90))
    use_energy = energy is not None and energy.size == env.size
    env_max = float(env.max()) if env.size else 0.0

    def local_peak(i: int, span: int = 2) -> int:
        """Вершина карты рядом с i: трекинг часто попадает в спад.

        Удар — это пик; ок, найденный в окне поиска, может оказаться
        краем плато (удар длится доли окна). Сдвигаем на настоящую
        вершину — иначе половина ударов не считается ударами.
        """
        a = max(0, i - span)
        b = min(n, i + span + 1)
        if b <= a:
            return i
        return a + int(np.argmax(env[a:b]))

    def pick(a: int, b: int) -> int:
        """Вершина окна: по энергии, если она согласуется с картой."""
        if use_energy:
            j = a + int(np.argmax(energy[a:b]))
            if env[j] >= 0.15 * env_max:
                return j
        return local_peak(a + int(np.argmax(env[a:b])))

    def pick_at(i: int, span: int = 2) -> int:
        """Вершина рядом с известной позицией удара."""
        a = max(0, i - span)
        b = min(n, i + span + 1)
        if b <= a:
            return i
        if use_energy:
            j = a + int(np.argmax(energy[a:b]))
            if env[j] >= 0.15 * env_max:
                return j
        return local_peak(i)

    start = pick(0, n)
    beats = [start]
    cur = start
    while True:
        a = cur + gap_lo
        b = min(n, cur + gap_hi)
        if a >= n:
            break
        seg = env[a:b]
        if seg.size == 0:
            break
        nxt = pick(a, b)
        if nxt <= cur:
            break
        beats.append(nxt)
        cur = nxt
    peaks = np.array(sorted(set(beats)), dtype=int)
    if peaks.size < 4:
        return peaks, 0.0, 0.0
    diffs = np.diff(peaks).astype(float)
    period = float(np.median(diffs))
    if period <= 0:
        return peaks, 0.0, 0.0
    # Удары до самого сильного пика: трек начался раньше, чем мы
    # нашли самую громкую точку. Период берём дробным — округлённый
    # целым накапливает ошибку: за десять ударов уезжает на пол-
    # секунды, и сетка перестаёт попадать в бит.
    back = []
    t = start - period
    while t >= 0:
        back.append(pick_at(int(round(t))))
        t -= period
    if start - period < 0 <= start + period:
        # Трек начинается ударом в самом начале: крайние окна не
        # видят фронт, удар там теряется — возвращаем его.
        back.append(0)
    if back:
        peaks = np.array(sorted(set(back) | set(peaks.tolist())), dtype=int)
        diffs = np.diff(peaks).astype(float)
        period = float(np.median(diffs))
    # Положение удара уточняем по энергии: карта разности спектра
    # реагирует на фронт удара и «звенит» за пару окон до него, из-за
    # чего удар встаёт на пару кадров раньше настоящего. Энергия
    # максимальна там, где удар реально звучит. Окно узкое (четыре
    # кадра), чтобы не утянуть удар на соседний.
    if use_energy:
        emax = float(energy.max())
        fixed = []
        for p in peaks:
            a = max(0, int(p) - 2)
            b = min(energy.size, int(p) + 5)
            j = a + int(np.argmax(energy[a:b]))
            fixed.append(j if energy[j] >= 0.15 * emax else int(p))
        peaks = np.array(sorted(set(fixed)), dtype=int)
        diffs = np.diff(peaks).astype(float)
        if diffs.size:
            period = float(np.median(diffs))
    close = _grid_fit(peaks, period)
    # Настоящесть ударов: трекинг на шуме тоже выдаёт ровные точки,
    # но это не пики карты. Удар — это локальный максимум и он
    # заметно выше фона. Допуск нужен для плато: удар длится доли
    # окна, и соседний отсчёт может быть ровно таким же.
    real = 0
    for p in peaks:
        prev = env[p - 1] if p > 0 else -1.0
        nxt = env[p + 1] if p + 1 < env.size else -1.0
        if env[p] >= prev - 0.03 and env[p] >= nxt - 0.03 and env[p] >= top:
            real += 1
    beatiness = real / float(peaks.size)
    return peaks, close, beatiness


def grid(path: Path = None, x: np.ndarray = None, rate: int = RATE,
         div: int = 2):
    """Сетка монтажа по музыке.

    Возвращает {"bpm":…, "period": секунды, "beats": удары, "times":
    доли (удары и их середины), "dur": длительность}. Пустой
    словарь — ритма не нашли.

    div — на сколько делим промежуток между ударами: 1 — только
    удары, 2 — ещё и середины (восьмые), 4 — шестнадцатые.
    """
    if x is None:
        if path is None:
            return {}
        x = read_mono(Path(path), rate=rate)
    if x.size == 0:
        return {}
    dur = float(x.size) / float(rate)
    env, fps = onset_envelope(x, rate=rate)
    if env.size < 32:
        return {}
    x_low = low_band(x, rate=rate)
    env_low, _ = onset_envelope(x_low, rate=rate)
    energy, _ = energy_map(x, rate=rate)
    lag, env_use = pick_lag(env, env_low, fps, energy)
    if lag <= 0:
        return {}
    peaks, regular, beatiness = find_beats(env_use, lag, energy)
    if peaks.size < 4 or regular < 0.6 or beatiness < 0.7:
        return {}
    # Период в секундах: медиана промежутков между ударами.
    diffs = np.diff(peaks).astype(float) / fps
    period = float(np.median(diffs))
    if period <= 0:
        return {}
    bpm = 60.0 / period
    if bpm < MIN_BPM * 0.9 or bpm > MAX_BPM * 1.1:
        return {}
    beats = [float(p) / fps for p in peaks]
    # Пропущенный удар (пауза в музыке) достраиваем: иначе в этом
    # месте не к чему привязать монтаж.
    filled = []
    for i, b in enumerate(beats):
        filled.append(b)
        if i + 1 < len(beats):
            gap = beats[i + 1] - b
            if gap > 1.6 * period:
                parts = int(round(gap / period))
                if parts >= 2:
                    for k in range(1, parts):
                        filled.append(b + gap * k / parts)
    beats = filled
    # Трек нередко начинается ударом, и крайние окна карты его не
    # видят: сетка «по найденным ударам» тогда сдвинута на часть
    # периода, а в начале монтажной сетки дыра. Если в начале громко
    # и первый найденный удар ложится на сетку от нуля — строим
    # ровную сетку от нуля.
    if energy.size >= 3 and float(energy[:3].max()) >= 0.7 * float(energy.max()):
        k = int(round(beats[0] / period)) if beats else 0
        if k >= 1 and abs(beats[0] - k * period) <= 0.3 * period:
            beats = [i * period for i in range(int(dur / period) + 2)
                     if i * period <= dur + 1e-9]
    times = []
    for i, b in enumerate(beats):
        times.append(round(b, 4))
        if div > 1 and i + 1 < len(beats):
            gap = beats[i + 1] - b
            steps = int(round(gap / (period / div)))
            for k in range(1, max(1, steps)):
                times.append(round(b + gap * k / max(1, steps), 4))
    times = sorted(set(times))
    return {"bpm": round(bpm, 2), "period": period,
            "beats": [round(b, 4) for b in beats],
            "times": times, "dur": dur}


def snap(t: float, times, tol: float = 0.4):
    """Ближайшая доля сетки к моменту t.

    Допуск: если ни одна доля не ближе tol, оставляем t как есть —
    лучше монтаж без ритма, чем монтаж, натянутый на случайный бит.
    """
    if not times:
        return t
    import bisect
    i = bisect.bisect_left(times, t)
    best = None
    for j in (i - 1, i, i + 1):
        if 0 <= j < len(times):
            d = abs(times[j] - t)
            if best is None or d < best[0]:
                best = (d, times[j])
    if best is None or best[0] > tol:
        return t
    return best[1]


def snap_forward(t: float, times, tol: float = 0.4):
    """Ближайшая доля, но не раньше t (если есть такая в пределах tol)."""
    if not times:
        return t
    import bisect
    cand = []
    for j in (bisect.bisect_left(times, t - tol) - 1, bisect.bisect_left(times, t)):
        if 0 <= j < len(times):
            cand.append(times[j])
    if not cand:
        return t
    best = None
    for c in cand:
        d = abs(c - t)
        if best is None or d < best[0]:
            best = (d, c)
    if best[0] > tol:
        return t
    return best[1]

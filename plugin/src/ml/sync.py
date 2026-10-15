#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoCut Sync — сводим мультикамеру на общую шкалу времени по звуку.

Дано: общий план (со своим звуком — ОПОРА), камера гостя, микрофон гостя,
микрофон ведущего. Всё стартовало в разное время.
Находим сдвиг каждого файла относительно опоры кросскорреляцией огибающих
и выдаём синхронизированные копии (добивка тишиной/чёрным в начало,
подрезка хвоста не делается — хвост просто длиннее).

Использование:
  python sync.py --ref общий.mp4 --cam гость.mp4 --mic-guest гость.wav \
      --mic-host ведущий.wav --outdir synced
  # выйдет: synced/ref.mp4, synced/cam.mp4, synced/mic_guest.wav,
  #         synced/mic_host.wav + offsets.json (сдвиги в секундах)

Дальше эти файлы идут в interview.py как обычно — времена уже общие.

Честные ограничения:
  - Нужен ОБЩИЙ звук: хлопок в начале, вопрос ведущего, смех зала. Два файла,
    записанные в разных комнатах, не сведутся — корреляции не будет.
  - Отчёт показывает качество пика (0..1): < 0.3 — не верь, сверь по хлопку вручную.
  - Дрейф часов за 5 минут ничтожен, не правим. На 40+ минутах скажи — добавлю.
"""

from __future__ import annotations
import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bootstrap  # noqa: F401  — регистрирует src/config в sys.path
import settings

# Пороги доверия к найденному сдвигу — из config/settings.json, секция sync.
# Пик считается слабым, если quality ниже порога И contrast ниже порога:
# гул в начале записи даёт низкий quality, но острый пик — такой сдвиг
# обычно верный, поэтому одного quality мало, чтобы объявить провал.
TRUST_QUALITY = float(settings.get("sync.trust_quality", 0.3))
TRUST_CONTRAST = float(settings.get("sync.trust_contrast", 3.0))
# Микрофоны не сошлись через опору — срезаем доверие и предупреждаем
MIC_MISMATCH_QUALITY = float(settings.get("sync.mic_mismatch_quality", 0.2))
MIC_MISMATCH_CONTRAST = float(settings.get("sync.mic_mismatch_contrast", 2.0))
MIC_MISMATCH_TOLERANCE = float(settings.get("sync.mic_cross_check_tolerance_sec", 1.0))
# В каком качестве писать МИКРОФОНЫ В РЕЗУЛЬТАТЕ. Это не то же самое, что
# `to_mono16k`: для разбора огибающей 16 кГц моно хватает и даже полезно
# (считать быстрее), но в папку synced кладётся исходная запись, приведённая
# к качеству экспорта. Иначе в Premiere микрофон звучит как разговор по
# телефону. Исходники лежат в 48 кГц, терять нечего.
AUDIO_RATE = int(settings.get("export.audio_sample_rate", 48000))
AUDIO_DEPTH = int(settings.get("export.audio_depth", 16))
AUDIO_CODEC = "pcm_s16le" if AUDIO_DEPTH == 16 else "pcm_s24le"
# Окно итераций и блоков в многосекундных единицах
VOTE_BLOCK_SEC = float(settings.get("sync.vote_block_sec", 60))
REFINE_WINDOW_SEC = float(settings.get("sync.refine_window_sec", 3))

# Допустимый разброс между попарными измерениями в глобальном согласовании.
# Если все пары дают один и тот же набор сдвигов, расхождение будет
# крошечным (десятые доли секунды на 10 минутах записи). Превышение
# означает, что часть пар совпала случайно — доверять нельзя.
GLOBAL_MAX_RESIDUAL = float(settings.get("sync.global_max_residual_sec", 1.5))
# Окно поиска в глобальном согласовании. НЕ выводим из разницы длительностей:
# файлы часто режутся до одинаковой длины, а реальные сдвиги при этом большие.
GLOBAL_MAX_LAG = float(settings.get("sync.global_max_lag_sec", 600.0))
# Минимальная острота пика, при которой измерению можно верить. Без этой
# проверки нули от неудачных измерений идеально «сходятся» и выдают
# правдоподобный, но выдуманный результат.
GLOBAL_MIN_CONTRAST = float(settings.get("sync.global_min_contrast", 2.0))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def to_mono16k(src: Path, dst: Path):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src),
         "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
        check=True)


def read_wav(path: Path):
    import numpy as np
    with wave.open(str(path), "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def envelope(x, sr: int = 16000, win: float = 0.02, rate: float = None,
             hp: float = 0.2):
    """Огибающая громкости: убираем медленный дрейф/субсонику окном hp
    (голос от 100 Гц не трогаем), сглаживаем окном win, децимация до rate Гц.

    rate и hp — необязательные: старый поиск от опоры работает на значениях
    по умолчанию, глобальному согласованию нужна другая детализация.
    Без rate берём sync.envelope_rate_hz - раньше здесь стояло зашитое 50.
    """
    import numpy as np
    if rate is None:
        rate = float(settings.get("sync.envelope_rate_hz", 50.0))
    wbase = max(1, int(sr * hp))
    x = x - np.convolve(x, np.ones(wbase) / wbase, mode="same")
    w = max(1, int(sr * win))
    env = np.convolve(np.abs(x), np.ones(w) / w, mode="same")
    step = max(1, int(sr / rate))
    return env[::step], rate


def _winsor(x, blklen: int, q: float = 99.0):
    """Потолок по каждому блоку отдельно: громкий кусок (шуршание рекордера)
    не должен задавать масштаб всей записи, иначе речь ужмётся в лепёшку.
    Глобальный кап по файлу — та же ошибка, не делать."""
    import numpy as np
    y = x.copy()
    for i in range(0, len(x), blklen):
        b = y[i:i + blklen]
        cap = float(np.percentile(b, q))
        if cap > 0:
            y[i:i + blklen] = np.minimum(b, cap)
    return y


def _wcorr(a, b, lags):
    """Корреляция на заданных сдвигах. corr[l] = sum a[i]*b[i-l].
    Пик при l* означает: содержимое b сдвинуто относительно a на -l*
    (l* < 0: копия в b позже — b стартовал раньше, leading-extra;
     l* > 0: копия в b раньше — b стартовал позже, обрезанное начало)."""
    import numpy as np
    a = a - a.mean()
    b = b - b.mean()
    n, m = len(a), len(b)
    out = np.zeros(len(lags))
    for k, l in enumerate(lags):
        l = int(l)
        if l >= 0:
            hi = min(n - l, m)
            if hi > 0:
                out[k] = float(np.dot(a[l:l + hi], b[:hi]))
        else:
            hi = min(n, m + l)
            if hi > 0:
                out[k] = float(np.dot(a[:hi], b[-l:-l + hi]))
    return out


def find_offset(ref_env, other_env, rate: float = 50.0, max_lag: float = None):
    """Сдвиг other относительно ref (сек; минус = other стартовал раньше).
    Возвращает (offset, quality, contrast).
    Метод: референс режем на блоки по 60с, каждый голосует за свой лучший
    сдвиг (глобальный максимум), побеждает мода. Одиночные ложные пики
    (периодика, самоподобие, шум) рассыпаются по разным сдвигам, а верный
    повторяется из блока в блок. Доводка — точный проход в окне ±3с.
    quality — высота пика к полной энергии (низкая при шумном начале,
    это нормально). contrast — пик к фону (> 3 = остро). agree < 0.4
    (блоки не сошлись) — вердикту не верить, quality режется."""
    import numpy as np
    if max_lag is None:
        max_lag = float(settings.get("sync.max_lag_sec", 60.0))
    if np.all(ref_env == 0) or np.all(other_env == 0):
        return 0.0, 0.0, 0.0

    ref_env = _winsor(ref_env, int(VOTE_BLOCK_SEC * rate))
    other_env = _winsor(other_env, int(VOTE_BLOCK_SEC * rate))

    step = max(1, int(round(rate / 10)))
    rc, oc = ref_env[::step], other_env[::step]
    blk = int(VOTE_BLOCK_SEC * rate / step)  # блоки референса по 60 секунд
    maxc = int(max_lag * rate / step)
    lags_c = np.arange(-maxc, maxc + 1)

    energies = np.array([np.dot(b, b) for b in
                         (rc[i:i + blk] for i in range(0, len(rc), blk))])
    med_e = np.median(energies) if len(energies) else 0.0
    votes = []
    minlen = int(5 * rate / step)  # блоки короче 5с не голосуют
    for i in range(0, len(rc), blk):
        b = rc[i:i + blk]
        if np.dot(b, b) < 0.2 * med_e or len(b) < minlen:
            continue  # тихий или куцый блок не голосует
        cc = _wcorr(b, oc, lags_c)
        votes.append(lags_c[int(cc.argmax())])
    if not votes:
        return 0.0, 0.0, 0.0
    # мода с корзиной ~0.5с
    nbins = max(1, int(round(2 * maxc / max(1, 0.5 * rate / step))))
    hist, edges = np.histogram(votes, bins=nbins)
    bi = int(hist.argmax())
    lag_c = int((edges[bi] + edges[bi + 1]) / 2)
    agree = float(hist.max() / len(votes))

    win = int(REFINE_WINDOW_SEC * rate)
    lags_f = np.arange(int(lag_c * step) - win, int(lag_c * step) + win + 1)
    lags_f = lags_f[np.abs(lags_f) <= max_lag * rate]
    if len(lags_f) == 0:
        # окно уточнения вышло за пределы поиска: голосование указывало
        # на самый край, а уточнять вокруг него нечего. Раньше здесь
        # был np.argmin от пустого массива и программа падала целиком -
        # то есть на материале с редкими сдвигами синхронизация не
        # запускалась вовсе.
        return 0.0, 0.0, 0.0
    cf = _wcorr(ref_env, other_env, lags_f)
    cands = np.where(cf >= 0.99 * cf.max())[0]
    if len(cands) == 0:
        # пик есть, но ни один кандидат не дотянул до порога: считаем,
        # что надёжной находки нет
        return 0.0, 0.0, 0.0
    # Берём самый сильный из кандидатов, а не ближайший к нулю. С
    # широким окном появляются ложные пики на краю поиска, и правило
    # «ближе к нулю - значит правильнее» их пропускало: настоящий сдвиг
    # и артефакт оказывались в равном положении, и выигрывал артефакт.
    bj = int(cands[int(cf[cands].argmax())])
    lag, peak = lags_f[bj], cf[bj]
    denom = float(np.sqrt(((ref_env - ref_env.mean()) ** 2).sum() *
                          ((other_env - other_env.mean()) ** 2).sum()) + 1e-9)
    quality = min(1.0, float(peak / denom))
    contrast = float(cf.max() / (np.median(np.abs(cf)) + 1e-9))
    if agree < 0.4:
        quality = min(quality, TRUST_QUALITY)
    if abs(lag) > 0.98 * max_lag * rate:
        # пик на самом краю окна = артефакт окна, а не находка
        quality, contrast = 0.0, min(contrast, 1.0)
    return lag / rate, quality, contrast


def auto_window(ref_dur: float, other_dur: float, base: float) -> float:
    """Окно поиска из разницы длительностей: сдвиг не может превышать её
    плюс запас 30с. Границы 60..300."""
    if ref_dur > 0 and other_dur > 0:
        return min(300.0, max(base, abs(other_dur - ref_dur) + 30.0))
    return max(base, 60.0)


def file_dur(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=60).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def pad_audio_to_zero(src_wav: Path, offset: float, dst: Path):
    """Добиваем начало тишиной на offset (файл стартовал позже опоры).

    Тишину добавляет ffmpeg, а не numpy: раньше здесь был свой writer,
    жёстко зашитый на 16000 Гц, моно и 16 бит, и дорожка в 48 кГц
    проходила через него сменой формата по дороге. `adelay=...:all=1`
    сдвигает все каналы одинаково и работает при любой частоте, а
    приведение к частоте экспорта ffmpeg делает сам - пересэмплирование
    у него качественнее, чем было бы перебросом int16 в int16.
    """
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src_wav), "-vn",
           "-ar", str(AUDIO_RATE)]
    if offset > 0.01:
        cmd += ["-af", f"adelay={int(round(offset * 1000))}:all=1"]
    cmd += ["-c:a", AUDIO_CODEC, str(dst)]
    subprocess.run(cmd, check=True)


def vcodec_args():
    """h264_nvenc если есть (в разы быстрее на NVIDIA), иначе libx264."""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                             capture_output=True, text=True, timeout=30).stdout
        if "h264_nvenc" in out:
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "19"]
    except Exception:
        pass
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]


def run_video_encode(args_list):
    """ffmpeg с автооткатом NVENC -> libx264: если железо занято/чудит,
    молча едем на CPU, а не падаем."""
    try:
        subprocess.run(args_list, check=True,
                       capture_output=True, text=True, timeout=3600)
    except subprocess.CalledProcessError as e:
        if "h264_nvenc" in " ".join(args_list):
            print("NVENC не завёлся, кодирую на CPU (дольше, но надёжно)...")
            fixed = []
            skip = False
            for a in args_list:
                if skip:
                    skip = False
                    continue
                if a == "h264_nvenc":
                    fixed += ["libx264", "-preset", "veryfast", "-crf", "18"]
                    continue
                if a in ("-preset", "-cq"):
                    skip = True
                    continue
                fixed.append(a)
            subprocess.run(fixed, check=True,
                           capture_output=True, text=True, timeout=7200)
        else:
            raise


def video_params(path: Path):
    """(ширина, высота, fps) первого видеопотока. None, если не разобрали."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=60).stdout.split()
        w, h, rate = int(out[0]), int(out[1]), out[2]
        num, _, den = rate.partition("/")
        fps = float(num) / float(den or 1)
        return w, h, round(fps, 3)
    except Exception:
        return None


def parse_conform(spec: str):
    """'1920x1080@25' -> (1920, 1080, 25). Пустая строка — не приводить."""
    if not spec:
        return None
    try:
        res, _, fps = spec.partition("@")
        w, _, h = res.partition("x")
        return int(w), int(h), (float(fps) if fps else 0.0) or None
    except Exception:
        raise ValueError(f"Не понял формат приведения: {spec!r}. "
                         f"Ожидалось 1920x1080@25")


def measure_pair(a_env, b_env, rate: float, max_lag_sec: float):
    """Полная кросс-корреляция двух дорожек через FFT.

    Работает без хлопка: общий сигнал даёт сама речь и шум зала, а огибающая
    за 10 минут накапливает достаточно материала для острого пика.

    Возвращает (lag, contrast, peak, second_peak).
    lag > 0 означает: b стартовал ПОЗЖЕ a на lag секунд.
    """
    import numpy as np
    a = a_env - a_env.mean()
    b = b_env - b_env.mean()
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    max_valid = n // 2 - 1
    ml = int(max_lag_sec * rate)
    if ml <= 0 or max_valid <= 0:
        return 0.0, 0.0, 0.0, 0.0, False
    clamped = False
    if ml > max_valid:
        # окно не влезает в длину сигнала: ищем максимально возможное,
        # но честно сообщаем, что искали не всё
        ml = max_valid
        clamped = True
    # отрицательные лаги в хвосте, положительные в начале
    cc = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    cc = np.concatenate((cc[-ml:], cc[:ml + 1]))
    lags = np.arange(-ml, ml + 1)

    i = int(np.argmax(cc))
    peak = float(cc[i])
    bg = float(np.median(np.abs(cc)))
    contrast = peak / (bg + 1e-12)

    # второй пик не ближе 10 секунд — иначе это тот же самый максимум
    guard = min(int(10.0 * rate), max(1, len(cc) // 2))
    cc2 = cc.copy()
    a0, b0 = max(0, i - guard), min(len(cc), i + guard)
    cc2[a0:b0] = -np.inf
    second = float(cc2.max())
    # окно короче половины сигнала — пик может оказаться краем, не пиком
    usable = not clamped or (ml >= rate * 30)
    return float(lags[i]) / rate, float(contrast), peak, second, usable


def solve_global_offsets(measurements, names, ref_name):
    """Сводит попарные сдвиги к одному набору смещений (метод наименьших квадратов).

    measurements: {(a, b): lag} — lag означает "b позже a на lag секунд".
    Каждое измерение независимо; если все они согласуются между собой,
    решение верное. Возвращает (offsets, max_residual) либо (None, 0).
    """
    import numpy as np
    free = [n for n in names if n != ref_name]
    if not free:
        return {ref_name: 0.0}, 0.0
    index = {n: i for i, n in enumerate(free)}

    rows, rhs = [], []
    for (a, b), lag in measurements.items():
        row = np.zeros(len(free))
        # offset[b] - offset[a] = lag, offset[ref] = 0
        if b in index:
            row[index[b]] += 1.0
        if a in index:
            row[index[a]] -= 1.0
        rows.append(row)
        rhs.append(lag)

    A = np.array(rows)
    y = np.array(rhs)
    sol, *_ = np.linalg.lstsq(A, y, rcond=None)

    offsets = {ref_name: 0.0}
    for n in free:
        offsets[n] = float(sol[index[n]])
    residuals = [abs(float(A[i] @ sol) - y[i]) for i in range(len(y))]
    return offsets, (max(residuals) if residuals else 0.0)


def pad_video_to_zero(src: Path, offset: float, dst: Path, is_ref: bool = False,
                      conform: str = ""):
    """Видео: чёрные кадры + тишина в начало. Опора тоже может требовать
    подрезки, если кто-то стартовал раньше неё (offset < 0).

    conform: "1920x1080@25" — привести разрешение и частоту кадров.
    Нужен, когда камеры разные (например 4K50 против 1080p25): иначе в
    секвенцию попадёт клип другого размера и частоты.
    """
    vfilters = []
    if conform:
        w, h, fps = parse_conform(conform)
        vfilters.append(f"scale={w}:{h}:flags=lanczos")
        vfilters.append(f"fps={fps}")

    if offset <= 0.01:
        if not vfilters:
            if is_ref:
                run_video_encode(["ffmpeg", "-y", "-v", "error", "-i", str(src)] +
                                 vcodec_args() + ["-c:a", "aac", str(dst)])
            else:
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
                                "-c", "copy", str(dst)], check=True)
            return
        chain = ",".join(vfilters)
        run_video_encode(
            ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", chain] +
            vcodec_args() + ["-c:a", "aac", str(dst)])
        return

    vf = ",".join([f"tpad=start_duration={offset:.3f}:color=black"] + vfilters)
    run_video_encode(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src),
         "-vf", vf,
         "-af", f"adelay={int(offset * 1000)}:all=1"] +
        vcodec_args() + ["-c:a", "aac", str(dst)])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AutoCut Sync — общая шкала времени")
    ap.add_argument("--ref", required=True, help="опора (общий план со звуком)")
    ap.add_argument("--cam", default="", help="вторая камера (гость)")
    ap.add_argument("--mic-guest", default="", help="микрофон гостя")
    ap.add_argument("--mic-host", default="", help="микрофон ведущего")
    ap.add_argument("--outdir", default="synced", help="куда сложить синхрон")
    ap.add_argument("--conform", default="",
                    help="привести видео к общему виду: 1920x1080@25. "
                         "Нужно, когда камеры разные (4K50 против 1080p25), "
                         "иначе в секвенцию попадёт клип другого размера")
    ap.add_argument("--no-global", action="store_true",
                    help="не пытаться согласовать все дорожки между собой, "
                         "искать сдвиг только относительно опоры")
    ap.add_argument("--max-lag", type=float,
                    default=float(settings.get("sync.max_lag_sec", 60.0)),
                    help="макс. рассинхрон (сек)")
    ap.add_argument("--set-offset", action="append", default=[],
                    help="ручной сдвиг вместо автопоиска: --set-offset mic_guest=2.5 "
                         "(имена: cam, mic_guest, mic_host; + = файл стартовал позже опоры). "
                         "Нужно, если автопоиск дал качество < 0.3: замерь сдвиг по хлопку "
                         "в любом редакторе и впиши сюда.")
    args = ap.parse_args(argv)

    conform = ""
    if args.conform:
        parse_conform(args.conform)   # проверяем формат сразу, до работы
        conform = args.conform
    elif args.cam:
        # камеры разного размера/частоты — приводим гостя к параметрам опоры
        ref_v = video_params(Path(args.ref))
        if ref_v:
            conform = f"{ref_v[0]}x{ref_v[1]}@{ref_v[2]:g}"

    import numpy as np
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "_tmp"
    tmp.mkdir(exist_ok=True)

    jobs = [("ref", Path(args.ref))]
    if args.cam:
        jobs.append(("cam", Path(args.cam)))
    if args.mic_guest:
        jobs.append(("mic_guest", Path(args.mic_guest)))
    if args.mic_host:
        jobs.append(("mic_host", Path(args.mic_host)))
    for _, p in jobs:
        if not p.is_file():
            print(f"Нет файла: {p}")
            return 2

    print("Считаю огибающие...")
    envs = {}
    for name, p in jobs:
        w = tmp / f"{name}.wav"
        to_mono16k(p, w)
        envs[name], rate = envelope(read_wav(w))

    # опора может сама стартовать не первой: считаем все относительно ref,
    # затем сдвигаем шкалу так, чтобы минимум стал нулём
    raw = {"ref": 0.0}
    quals = {"ref": 1.0}
    contr = {"ref": 99.0}
    manual = {}
    for s in args.set_offset:
        if "=" in s:
            k, v = s.split("=", 1)
            try:
                manual[k.strip()] = float(v.strip())
            except ValueError:
                print(f"Не понял --set-offset {s!r}, пропускаю.")
    ref_dur = file_dur(Path(args.ref))

    # --- Шаг 1: глобальное согласование по всем парам сразу ---
    # Пара с общим сигналом даёт сдвиг независимо, а не относительно одной
    # «опоры». Если все пары согласуются между собой — решение верное,
    # и хлопок для этого не нужен.
    global_offsets = None
    if len(jobs) > 1 and not manual and not args.no_global:
        rates = []
        names = [n for n, _ in jobs]
        for rate_hz in (20.0, 50.0):
            envs2 = {}
            for name, p in jobs:
                w = tmp / f"{name}.wav"
                if not w.is_file():
                    to_mono16k(p, w)
                # окно сглаживания держим постоянным по времени (0.04 с),
                # меняется только частота дискретизации огибающей
                envs2[name], rate_now = envelope(read_wav(w), rate=rate_hz,
                                                 win=0.04, hp=0.3)
            rate = rate_now
            pairs = {}
            worst_contrast = None
            invalid = []
            dur_map = {n: file_dur(p) for n, p in jobs}
            for i in range(len(names)):
                for j in range(len(names)):
                    if i == j:
                        continue
                    an, bn = names[i], names[j]
                    window = min(GLOBAL_MAX_LAG, max(60.0,
                                                    max(dur_map.values()) + 30.0))
                    lag, contrast, peak, second, usable = measure_pair(
                        envs2[an], envs2[bn], rate, window)
                    pairs[(an, bn)] = lag
                    # измерение годится, только если пик настоящий и окно
                    # не было урезано до бесполезного размера
                    if not usable or contrast < GLOBAL_MIN_CONTRAST:
                        invalid.append(f"{an}<->{bn} (контраст {contrast:.1f})")
                    r = peak / (second + 1e-12)
                    if worst_contrast is None or r < worst_contrast:
                        worst_contrast = r
            offsets_g, residual = solve_global_offsets(pairs, names, "ref")
            rates.append((rate_hz, residual, worst_contrast, offsets_g, invalid))

        # берём детализацию, где попарные измерения сошлись лучше всего
        rates.sort(key=lambda t: t[1])
        rate_hz, residual, worst_ratio, offsets_g, invalid = rates[0]
        # принимаем только если все пары дали настоящий пик И сошлись между собой
        if residual <= GLOBAL_MAX_RESIDUAL and not invalid:
            global_offsets = offsets_g
            print(f"Глобальное согласование по {len(names)} дорожкам "
                  f"(огибающая {rate_hz:g} Гц):")
            for n in names:
                print(f"  {n:11} начало записи {offsets_g[n]:+8.2f}с "
                      f"(относительно ref)")
            print(f"  Максимальный разброс между парами: {residual:.2f}с "
                  f"(порог {GLOBAL_MAX_RESIDUAL}с). Сдвиги согласованы между собой.")
        else:
            why = []
            if invalid:
                why.append("не годные пики: " + ", ".join(invalid[:4]))
            if residual > GLOBAL_MAX_RESIDUAL:
                why.append(f"разброс {residual:.2f}с > {GLOBAL_MAX_RESIDUAL}с")
            print("Глобальное согласование не принято (" + "; ".join(why) +
                  ") — перехожу на поиск от опоры.")
            for rh, res, wr, _, inv in rates:
                print(f"    огибающая {rh:g} Гц: разброс {res:.2f}с, "
                      f"негодных пар {len(inv)}")

    if global_offsets is not None:
        raw = {n: global_offsets[n] for n, _ in jobs}
        quals = {n: 1.0 for n, _ in jobs}
        contr = {n: 99.0 for n, _ in jobs}
    else:
        raw = {"ref": 0.0}
        quals = {"ref": 1.0}
        contr = {"ref": 99.0}
        for name, p in jobs[1:]:
            if name in manual:
                raw[name], quals[name] = manual[name], 1.0
                contr[name] = 99.0
                print(f"  {name}: РУЧНОЙ сдвиг {manual[name]:+.2f}с")
                continue
            # окно из разницы длительностей: рекордер на 2 мин длиннее не может
            # отставать меньше чем на ~2 мин (плюс запас)
            window = auto_window(ref_dur, file_dur(p), args.max_lag)
            off, q, co = find_offset(envs["ref"], envs[name], rate, window)
            if (q < TRUST_QUALITY and co < TRUST_CONTRAST) and window < 600:
                # слабый пик: пробуем окно вдвое шире
                off2, q2, co2 = find_offset(envs["ref"], envs[name], rate,
                                            min(600.0, window * 2))
                if co2 > co:
                    off, q, co = off2, q2, co2
                    print(f"  {name}: узкий поиск провален, взят широкий.")
            raw[name], quals[name], contr[name] = round(off, 3), round(q, 3), round(co, 1)
            bad = q < TRUST_QUALITY and co < TRUST_CONTRAST
            if quals[name] == 0.0:
                raw[name] = 0.0
                print(f"  {name}: НЕ СВЕДЁН (в звуке нет общего сигнала). "
                      f"Замерь сдвиг по хлопку и перезапусти с --set-offset {name}=N.")
            print(f"  {name}: сдвиг {off:+.2f}с, качество {q:.2f}, контраст {co:.1f}"
                  + ("  <-- ПРОВЕРЬ ВРУЧНУЮ" if bad else
                     ("  (качество низкое от мусора в начале, но пик острый — скорее верно)"
                      if q < TRUST_QUALITY else "")))
    base = min(0.0, min(raw.values()))
    offsets = {k: round(v - base, 3) for k, v in raw.items()}
    if global_offsets is not None:
        print("Все дорожки согласованы между собой, сверять вручную не нужно.")
    else:
        # перекрёстная проверка: прямой сдвиг между микрофонами должен совпасть
        # с разностью их сдвигов от опоры (ловушки периодики парные и разные)
        if "mic_guest" in raw and "mic_host" in raw \
                and "mic_guest" not in manual and "mic_host" not in manual:
            gg, _ = envelope(read_wav(tmp / "mic_guest.wav"))
            hh, _ = envelope(read_wav(tmp / "mic_host.wav"))
            direct, _, cox = find_offset(gg, hh, rate,
                                         min(300.0, max(120.0, args.max_lag)))
            if abs((raw["mic_guest"] - raw["mic_host"]) - direct) > MIC_MISMATCH_TOLERANCE:
                print(f"  ВНИМАНИЕ: микрофоны не сходятся (через опору "
                      f"{raw['mic_guest'] - raw['mic_host']:+.1f}с, напрямую {direct:+.1f}с). "
                      f"СВЕРЬ ПО ХЛОПКУ, возьми ручные --set-offset.")
                for k in ("mic_guest", "mic_host"):
                    quals[k] = min(quals[k], MIC_MISMATCH_QUALITY)
                    contr[k] = min(contr[k], MIC_MISMATCH_CONTRAST)
            else:
                print(f"  Перекрёстная проверка микрофонов сошлась ({direct:+.2f}с).")
        if any(q < TRUST_QUALITY and contr.get(k, 0) < TRUST_CONTRAST
               for k, q in quals.items()):
            print("Слабые пики: СВЕРЬ сдвиги по хлопку! Если автомат врёт — перезапусти с ручными:")
            print("  sync.py ... --set-offset mic_guest=2.5 --set-offset mic_host=3.1")

    print("Добиваю до общего нуля...")
    for name, p in jobs:
        if name.startswith("mic_"):
            # Именно исходник `p`, а не промежуточный tmp/{name}.wav:
            # тот ради разбора, этот идёт в результат.
            pad_audio_to_zero(p, offsets[name], out / f"{name}.wav")
        else:
            pad_video_to_zero(p, offsets[name], out / f"{name}.mp4",
                              is_ref=(name == "ref"), conform=conform)
    (out / "offsets.json").write_text(
        json.dumps({"offsets": offsets, "quality": quals, "contrast": contr,
                    "method": "global" if global_offsets is not None else "reference",
                    "conform": conform,
                    "note": "method=global — сдвиги согласованы по всем парам дорожек, "
                            "хлопок не требовался и сверять вручную не нужно"},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Готово: {out} (сдвиги {offsets})")
    print(f"Дальше: plugin/src/modes/interview.py --wide {out/'ref.mp4'} --guest-cam {out/'cam.mp4'} ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

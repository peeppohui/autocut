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


def envelope(x, sr: int = 16000, win: float = 0.02):
    """Огибающая громкости: убираем медленный дрейф/субсонику окном 0.2с
    (голос от 100 Гц не трогаем), сглаживаем окном 20мс, глушим одиночные
    громкие стуки капом на 95-м перцентиле, децимация до ~50 Гц."""
    import numpy as np
    wbase = max(1, int(sr * 0.2))
    x = x - np.convolve(x, np.ones(wbase) / wbase, mode="same")
    w = max(1, int(sr * win))
    env = np.convolve(np.abs(x), np.ones(w) / w, mode="same")
    step = max(1, int(sr / 50))
    return env[::step], sr / step


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


def find_offset(ref_env, other_env, rate: float = 50.0, max_lag: float = 60.0):
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
    if np.all(ref_env == 0) or np.all(other_env == 0):
        return 0.0, 0.0, 0.0

    ref_env = _winsor(ref_env, int(60 * rate))
    other_env = _winsor(other_env, int(60 * rate))

    step = max(1, int(round(rate / 10)))
    rc, oc = ref_env[::step], other_env[::step]
    blk = int(60 * rate / step)  # 60-секундные блоки референса
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

    win = int(3 * rate)
    lags_f = np.arange(int(lag_c * step) - win, int(lag_c * step) + win + 1)
    lags_f = lags_f[np.abs(lags_f) <= max_lag * rate]
    cf = _wcorr(ref_env, other_env, lags_f)
    cands = np.where(cf >= 0.99 * cf.max())[0]
    bj = cands[np.abs(lags_f[cands]).argmin()]
    lag, peak = lags_f[bj], cf[bj]
    denom = float(np.sqrt(((ref_env - ref_env.mean()) ** 2).sum() *
                          ((other_env - other_env.mean()) ** 2).sum()) + 1e-9)
    quality = min(1.0, float(peak / denom))
    contrast = float(cf.max() / (np.median(np.abs(cf)) + 1e-9))
    if agree < 0.4:
        quality = min(quality, 0.3)
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
    """Добиваем начало тишиной на offset (файл стартовал позже опоры)."""
    if offset <= 0.01:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src_wav),
                        "-c:a", "pcm_s16le", str(dst)], check=True)
        return
    import numpy as np
    x = read_wav(src_wav)
    pad = np.zeros(int(offset * 16000), dtype=np.float32)
    y = np.concatenate([pad, x])
    import wave as wv
    with wv.open(str(dst), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())


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


def pad_video_to_zero(src: Path, offset: float, dst: Path, is_ref: bool = False):
    """Видео: чёрные кадры + тишина в начало. Опора тоже может требовать
    подрезки, если кто-то стартовал раньше неё (offset < 0)."""
    if offset <= 0.01 and is_ref:
        run_video_encode(["ffmpeg", "-y", "-v", "error", "-i", str(src)] +
                         vcodec_args() + ["-c:a", "aac", str(dst)])
        return
    if offset <= 0.01:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
                        "-c", "copy", str(dst)], check=True)
        return
    run_video_encode(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src),
         "-vf", f"tpad=start_duration={offset:.3f}:color=black",
         "-af", f"adelay={int(offset * 1000)}:all=1"] +
        vcodec_args() + ["-c:a", "aac", str(dst)])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AutoCut Sync — общая шкала времени")
    ap.add_argument("--ref", required=True, help="опора (общий план со звуком)")
    ap.add_argument("--cam", default="", help="вторая камера (гость)")
    ap.add_argument("--mic-guest", default="", help="микрофон гостя")
    ap.add_argument("--mic-host", default="", help="микрофон ведущего")
    ap.add_argument("--outdir", default="synced", help="куда сложить синхрон")
    ap.add_argument("--max-lag", type=float, default=60.0, help="макс. рассинхрон (сек)")
    ap.add_argument("--set-offset", action="append", default=[],
                    help="ручной сдвиг вместо автопоиска: --set-offset mic_guest=2.5 "
                         "(имена: cam, mic_guest, mic_host; + = файл стартовал позже опоры). "
                         "Нужно, если автопоиск дал качество < 0.3: замерь сдвиг по хлопку "
                         "в любом редакторе и впиши сюда.")
    args = ap.parse_args(argv)

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
        if (q < 0.3 and co < 3) and window < 600:
            # слабый пик: пробуем окно вдвое шире
            off2, q2, co2 = find_offset(envs["ref"], envs[name], rate, min(600.0, window * 2))
            if co2 > co:
                off, q, co = off2, q2, co2
                print(f"  {name}: узкий поиск провален, взят широкий.")
        raw[name], quals[name], contr[name] = round(off, 3), round(q, 3), round(co, 1)
        bad = q < 0.3 and co < 3
        if quals[name] == 0.0:
            raw[name] = 0.0
            print(f"  {name}: НЕ СВЕДЁН (в звуке нет общего сигнала). "
                  f"Замерь сдвиг по хлопку и перезапусти с --set-offset {name}=N.")
        print(f"  {name}: сдвиг {off:+.2f}с, качество {q:.2f}, контраст {co:.1f}"
              + ("  <-- ПРОВЕРЬ ВРУЧНУЮ" if bad else
                 ("  (качество низкое от мусора в начале, но пик острый — скорее верно)" if q < 0.3 else "")))
    base = min(0.0, min(raw.values()))
    offsets = {k: round(v - base, 3) for k, v in raw.items()}
    # перекрёстная проверка: прямой сдвиг между микрофонами должен совпасть
    # с разностью их сдвигов от опоры (ловушки периодики парные и разные)
    if "mic_guest" in raw and "mic_host" in raw \
            and "mic_guest" not in manual and "mic_host" not in manual:
        gg, _ = envelope(read_wav(tmp / "mic_guest.wav"))
        hh, _ = envelope(read_wav(tmp / "mic_host.wav"))
        direct, _, cox = find_offset(gg, hh, rate, min(300.0, max(120.0, args.max_lag)))
        if abs((raw["mic_guest"] - raw["mic_host"]) - direct) > 1.0:
            print(f"  ВНИМАНИЕ: микрофоны не сходятся (через опору "
                  f"{raw['mic_guest'] - raw['mic_host']:+.1f}с, напрямую {direct:+.1f}с). "
                  f"СВЕРЬ ПО ХЛОПКУ, возьми ручные --set-offset.")
            for k in ("mic_guest", "mic_host"):
                quals[k] = min(quals[k], 0.2)
                contr[k] = min(contr[k], 2.0)
        else:
            print(f"  Перекрёстная проверка микрофонов сошлась ({direct:+.2f}с).")
    if any(q < 0.3 and contr.get(k, 0) < 3 for k, q in quals.items()):
        print("Слабые пики: СВЕРЬ сдвиги по хлопку! Если автомат врёт — перезапусти с ручными:")
        print("  sync.py ... --set-offset mic_guest=2.5 --set-offset mic_host=3.1")

    print("Добиваю до общего нуля...")
    is_video = {"ref": True, "cam": bool(args.cam)}
    for name, p in jobs:
        if name.startswith("mic_"):
            pad_audio_to_zero(tmp / f"{name}.wav", offsets[name], out / f"{name}.wav")
        else:
            pad_video_to_zero(p, offsets[name], out / f"{name}.mp4",
                              is_ref=(name == "ref"))
    (out / "offsets.json").write_text(
        json.dumps({"offsets": offsets, "quality": quals, "contrast": contr,
                    "note": "quality<0.3 при contrast>3 — нормально для рекордеров "
                            "с шумным началом; всё равно сверь по хлопку"},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Готово: {out} (сдвиги {offsets})")
    print(f"Дальше: plugin/src/modes/interview.py --wide {out/'ref.mp4'} --guest-cam {out/'cam.mp4'} ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

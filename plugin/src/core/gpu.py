#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GPU-хелпер: находит CUDA DLL из pip-пакетов и выбирает устройство.

Использование:
    from gpu import pick
    device = pick("auto")   # "cuda" если завелась, иначе "cpu"
    device = pick("cpu")    # принудительно CPU
    device = pick("cuda")   # принудительно GPU (упадёт с понятной ошибкой если нет)
"""

import os
import sys
from pathlib import Path


def bootstrap():
    """Прописываем CUDA DLL (Windows): site-packages/nvidia/*/bin|lib."""
    if sys.platform != "win32":
        return
    try:
        import site as _site
        roots = []
        try:
            roots += _site.getsitepackages()
        except Exception:
            pass
        try:
            roots.append(_site.getusersitepackages())
        except Exception:
            pass
        for sp in roots:
            nb = Path(sp) / "nvidia"
            if not nb.is_dir():
                continue
            for sub in nb.iterdir():
                for d in (sub / "bin", sub / "lib"):
                    if d.is_dir():
                        try:
                            os.add_dll_directory(str(d))
                        except Exception:
                            pass
                        if str(d) not in os.environ.get("PATH", ""):
                            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


def cuda_ok() -> bool:
    """Есть ли рабочая CUDA для ctranslate2 (whisper) или torch (yolo)."""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return True
    except Exception:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            return True
    except Exception:
        pass
    return False


def pick(want: str = "auto") -> str:
    """Возвращает "cuda" или "cpu". Никогда не падает: при проблемах — cpu."""
    bootstrap()
    want = (want or "auto").lower()
    if want == "cpu":
        return "cpu"
    if want == "cuda":
        return "cuda"  # явно просили — пусть упадёт с понятной ошибкой, а не молча
    return "cuda" if cuda_ok() else "cpu"

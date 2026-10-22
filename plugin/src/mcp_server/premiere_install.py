# -*- coding: utf-8 -*-
"""Установка панели AutoCut в Premiere Pro.

Панель CEP кладётся в папку расширений пользователя, и ещё включается
режим отладки в реестре — без него Premiere не показывает неподписанные
панели.

Пути:
* панель: `%APPDATA%\\Adobe\\CEP\\extensions\\AutoCutBridge`
* реестр: `HKEY_CURRENT_USER\\Software\\Adobe\\CSXS.<N>\\PlayerDebugMode`
  для N = 9…14, значение — строка "1"

Ключи реестра нужны и подписанной панели: без них Premiere не грузит
расширения CEP вообще, даже установленные через Extension Manager.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
PANEL_SRC = SRC.parent / "premiere_panel"
EXT_NAME = "AutoCutBridge"
CSXS_VERSIONS = range(9, 15)


def panel_target() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Adobe" / "CEP" / "extensions" / EXT_NAME


def install_panel() -> dict:
    """Скопировать панель и включить режим отладки. Возвращает отчёт."""
    out = {"установлено": False, "путь": str(panel_target()), "ошибки": []}
    if not PANEL_SRC.is_dir():
        out["ошибки"].append("нет папки панели: " + str(PANEL_SRC))
        return out
    dst = panel_target()
    try:
        if dst.exists():
            shutil.rmtree(dst, ignore_errors=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(PANEL_SRC, dst)
        out["установлено"] = True
    except Exception as e:
        out["ошибки"].append("копирование: " + str(e))
        return out

    # Реестр: без PlayerDebugMode Premiere не грузит панели CEP.
    if sys.platform != "win32":
        out["ошибки"].append("реестр — только для Windows")
        return out
    try:
        import winreg
        for n in CSXS_VERSIONS:
            key = r"Software\Adobe\CSXS.{}".format(n)
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
                winreg.SetValueEx(k, "PlayerDebugMode", 0, winreg.REG_SZ, "1")
        out["реестр"] = "ключи CSXS.{} поставлены".format(
            ",".join(str(n) for n in CSXS_VERSIONS))
    except Exception as e:
        out["ошибки"].append("реестр: " + str(e))
    out["как_открыть"] = ("Premiere -> Окно (Window) -> Расширения (Extensions) "
                         "-> AutoCut, панель должна показать «на связи»")
    out["нужно_перезапустить"] = True
    return out


def is_installed() -> bool:
    return (panel_target() / "manifest.xml").is_file()

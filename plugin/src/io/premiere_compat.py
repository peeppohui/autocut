# -*- coding: utf-8 -*-
"""Закрыть окно «System Compatibility Report» в Premiere.

Premiere 26 на Windows 10 IoT LTSC (сборка 19044) при каждом запуске
показывает окно «System Compatibility Report»: система больше не
поддерживается. Окно модальное — пока оно на экране, Premiere не
грузит проект и не поднимает панели.

Отметка «Continue with known issues» не помогает: проверено, окно
приходит снова и снова. И вычистить список известных проблем нельзя:
приложение переписывает его в профиле при каждом запуске. Единственное,
что работает, — сторож: окно живёт несколько секунд, и если его сразу
закрыть, оно не мешает.

Кнопку в окне найти нельзя: Adobe рисует её сама, и ни UI Automation,
ни Win32 её не видят — в дереве окна лежит только контейнер
«OS_ViewContainer». Поэтому жмём по координатам: «Continue with known
issues» стоит в правом нижнем углу.

Как пользоваться:

    python -m src.io.premiere_compat            # закрыть, если появилось
    python -m src.io.premiere_compat --watch    # следить и закрывать

Сторож ставится в автозагрузку: cmd в папке Startup, который запускает
pythonw без окна. Тогда окно не появляется вовсе.
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

# Доля окна, где лежит кнопка «Continue with known issues».
BTN_X = 0.745
BTN_Y = 0.885

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Объявления обязательны: без них 64-битный HANDLE обрезается до
# 32 бит, и вызов молча возвращает мусор.
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                             ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                               wintypes.DWORD, ctypes.c_void_p]
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _class_of(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(wintypes.HWND(hwnd), buf, 256)
    return buf.value


def _is_premiere(pid: int) -> bool:
    """Процесс — это Premiere?

    Через QueryFullProcessImageName, а не через tasklist: сторож
    проверяет каждые полсекунды, и запускать процесс на каждой
    проверке слишком тяжело.
    """
    try:
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False,
                                 int(pid))
        if not h:
            return False
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf,
                                                   ctypes.byref(size)):
                return "premiere" in buf.value.lower()
            return False
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return False


def _dialog_handles():
    """Окна Premiere-класса #32770, внутри которых контейнер отчёта."""
    out = []
    hits = []

    def child_proc(hwnd, lparam):
        if "DroverLord" in _class_of(hwnd):
            hits.append(hwnd)
        return True

    def win_proc(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not _is_premiere(pid.value):
            return True
        if _class_of(hwnd) != "#32770":
            return True
        hits.clear()
        user32.EnumChildWindows(hwnd, ENUMPROC(child_proc), 0)
        if hits:
            out.append(hwnd)
        return True

    user32.EnumWindows(ENUMPROC(win_proc), 0)
    return out


def _click(hwnd) -> bool:
    """Жмём «Continue with known issues» в окне отчёта."""
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return False
    x = rect.left + int(w * BTN_X)
    y = rect.top + int(h * BTN_Y)
    user32.SetCursorPos(x, y)
    time.sleep(0.2)
    user32.mouse_event(0x0002, 0, 0, 0, None)      # LEFTDOWN
    time.sleep(0.08)
    user32.mouse_event(0x0004, 0, 0, 0, None)      # LEFTUP
    return True


def dismiss() -> bool:
    """Закрыть окно отчёта, если оно есть. True — закрыто."""
    for hwnd in _dialog_handles():
        if _click(hwnd):
            return True
    return False


def watch(seconds: int = 0) -> None:
    """Следить и закрывать. seconds=0 — пока не остановят."""
    print("Слежу за окном совместимости Premiere. Остановить — Ctrl+C.",
          file=sys.stderr)
    while True:
        try:
            dismiss()
        except Exception:
            pass
        time.sleep(0.5)
        if seconds and time.time() % 3600 > seconds:
            return


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--watch" in argv:
        watch()
        return 0
    print("закрыто" if dismiss() else "окна не было")
    return 0


if __name__ == "__main__":
    sys.exit(main())

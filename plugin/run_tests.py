# -*- coding: utf-8 -*-
"""Прогон всех тестов так, чтобы падение нельзя было принять за успех.

Раньше проверка была `-match "OK"` по выводу. Это ложь: упавший тест
успевает напечатать несколько строк OK и лишь потом падает, а regex
видел OK и считал файл зелёным. Один упавший тест так и прошёл.

Здесь признак успеха - код возврата 0 И маркер в конце вывода.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent        # plugin/
TESTS = ROOT / "tests"
MARK = re.compile(r"([A-Z][A-Z0-9 ]*)\s+OK\s*$", re.M)


def run(path: Path, timeout: int = 300):
    try:
        p = subprocess.run([sys.executable, str(path)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        # тест завис (например, открыл модальное окно). Раньше такой
        # случай молчал и прогон висел вместе с ним.
        return None, False, f"ЗАВИС БОЛЕЕ {timeout}с", ""
    out = p.stdout or ""
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    marked = bool(MARK.search(out))
    return p.returncode, marked, tail, (p.stderr or "").strip()


def main() -> int:
    files = sorted(TESTS.glob("test_*.py"))
    if not files:
        print("Тестов нет")
        return 1
    bad = []
    for f in files:
        code, marked, tail, err = run(f)
        ok = (code == 0) and marked
        print(f"  {'OK   ' if ok else 'СБОЙ '} {f.name:<24} "
              f"{'код 0' if code == 0 else tail[:40]:<6} {tail[:34]}")
        if not ok:
            bad.append(f.name)
            if err:
                for line in err.splitlines()[-6:]:
                    print(f"         {line}")
    print()
    if bad:
        print(f"ПРОВАЛЕНО {len(bad)} из {len(files)}: {bad}")
        return 1
    print(f"Все {len(files)} тестов прошли: код 0 и маркер в конце")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
# -*- coding: utf-8 -*-
"""Никаких настроек-пустышек.

Ключ в settings.json имеет смысл только если его читает код. Иначе файл
обещает то, чего не происходит: человек правит число, а монтаж
меняется как-то ещё, и через час он уже не понимает, что трогал.
Особенно это касалось окна «Настройки» - там были ползунки, которые
никто не читал.

Проверка идёт по факту: значение, которое пишет окно, обязано где-то
читаться модулем движка, а не самим окном.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC / "config"))
sys.path.insert(0, str(SRC / "ui"))
import bootstrap  # noqa: F401,F403

import settings

fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"OK   {name}")
    else:
        fail.append(name)
        print(f"FAIL {name}  {detail}")


cfg = json.loads((SRC / "config" / "settings.json").read_text(encoding="utf-8"))

# --- пути: все (для проверки отсутствующих) и только значения (для мёртвых) ---
all_paths, leaf_paths = set(), set()


def walk(node, prefix=""):
    for k, v in node.items():
        if k.startswith("_"):
            continue
        path = f"{prefix}{k}"
        all_paths.add(path)
        if isinstance(v, dict):
            walk(v, f"{path}.")
        else:
            leaf_paths.add(path)


walk(cfg)

sources = [p for p in SRC.rglob("*.py")
           if "__pycache__" not in str(p) and p.name != "settings.py"]
blob = "\n".join(p.read_text(encoding="utf-8") for p in sources)
dotted = set(re.findall(r'settings\.get\(\s*"([^"]+)"', blob))
sections = set(re.findall(r'settings\.section\(\s*"([^"]+)"', blob))
live = dotted | {k for k in all_paths
                 if any(k == s or k.startswith(s + ".") for s in sections)}

# rec_duration_sec читается как словарь целиком, поэтому его дети живут
# вместе с родителем, а не по отдельности
parents = {p for p in dotted if "." in p}
live |= {k for k in all_paths
         if any(k.startswith(p + ".") for p in parents)}

dead = sorted(leaf_paths - live)
missing = sorted(dotted - all_paths)

ok("в settings.json нет нечитаемых ключей", not dead, str(dead))
ok("все ключи, которые читает код, есть в файле", not missing, str(missing))
ok("шапка файла не врёт про чтение",
   "ничего не читается" not in
   (cfg.get("_readme") or [""])[0].lower()
   and "ничего не читается" not in " ".join(cfg.get("_readme") or []).lower(),
   str(cfg.get("_readme")))

# --- окно «Настройки» не должно править тем, на что никто не смотрит ---
import app as A  # noqa: E402

body = A.page_settings.__doc__ or ""
ok("окно настроек вообще есть", hasattr(A, "page_settings"), "")

src = Path(A.__file__).read_text(encoding="utf-8")
block = src[src.index("def page_settings"):src.index("def page_digest")]
written = re.findall(r'\("([^"]+)",\s*\w+\)', block)
ok("окно пишет хотя бы восемь настроек", len(written) >= 8, str(len(written)))

engine = "\n".join(p.read_text(encoding="utf-8")
                   for p in sources if p.parent.name != "ui")
unguarded = [k for k in written
             if k not in engine and not k.startswith("black.")]
ok("каждый ползунок окна читается движком, а не самим окном",
   not unguarded, str(unguarded))

# --- новые ключи не должны ломать чтение ---
with tempfile.TemporaryDirectory() as td:
    for k in ("sync.global_max_lag_sec", "sync.envelope_rate_hz",
              "music.sample_rate", "export.write_edl", "export.markers",
              "interview.target_sec", "interview_cuts.shot_optimal_min_sec"):
        v = settings.get(k, None)
        ok(f"ключ {k} читается и не пуст", v is not None, str(v))

print()
if fail:
    print(f"SETTINGS FAIL ({len(fail)}): {fail}")
    sys.exit(1)
print("SETTINGS OK")
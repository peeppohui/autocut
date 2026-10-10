"""Чтение настроек AutoCut из config/settings.json.

Стандартная библиотека, без зависимостей. Кэширует файл на уровне модуля,
чтобы не читать JSON на каждый кадр.

Пока модули не читают настройки (это M1). Здесь только загрузчик, чтобы
структура была готова.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"

_cache: Dict[str, Any] | None = None


def load(path: Path | None = None, *, refresh: bool = False) -> Dict[str, Any]:
    """Читает settings.json. Кэширует результат; refresh=True — перечитать."""
    global _cache
    if path is None and _cache is not None and not refresh:
        return _cache

    target = Path(path) if path is not None else SETTINGS_PATH
    if not target.is_file():
        raise FileNotFoundError(f"Файл настроек не найден: {target}")

    data = json.loads(target.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"Ожидался JSON-объект в корне {target}")

    data = _normalize(data)

    if path is None:
        _cache = data
    return data


def _is_num_pair(value: list) -> bool:
    return (len(value) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in value))


def _normalize(node: Any) -> Any:
    """JSON не умеет кортежи: [4.0, 8.0] читается как список, а код ждёт tuple.

    Приводим списки из двух чисел к кортежам — тогда значения из settings.json
    можно подставлять вместо констант без смены типа, и сравнения в тестах
    проходят без приведения.
    """
    if isinstance(node, dict):
        return {k: _normalize(v) for k, v in node.items()}
    if isinstance(node, list):
        items = [_normalize(v) for v in node]
        return tuple(items) if _is_num_pair(items) else items
    return node


def section(name: str, *, path: Path | None = None) -> Dict[str, Any]:
    """Возвращает секцию настроек по имени. Пусто, если секции нет."""
    data = load(path)
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"Секция «{name}» должна быть объектом")
    return value


def get(dotted: str, default: Any = None, *, path: Path | None = None) -> Any:
    """Достаёт значение по точечному пути: get("detect.face_w_detail")."""
    node: Any = load(path)
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node
"""Pool радиостанций — URL снятые через боевой mail.ru /phrase/create/text.

Голосовой триггер «Маруся, включи <X>» ищет совпадение по подстроке
(нормализованной: нижний регистр, замена ё→е, удаление «радио»).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_STATIONS: dict[str, dict] = {}


def _load() -> dict:
    global _STATIONS
    if _STATIONS:
        return _STATIONS
    path = os.environ.get("MARUSIA_RADIO_FILE", "/app/data/radio_stations.json")
    try:
        _STATIONS = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        _STATIONS = {}
    return _STATIONS


def _norm(s: str) -> str:
    s = s.lower().replace("ё", "е")
    for junk in ("радио", "фм", " fm", "включи", "поставь"):
        s = s.replace(junk, " ")
    return " ".join(s.split())


def find(query: str) -> dict | None:
    """Ищет станцию по подстроке в нормализованном имени.
    Возвращает dict со всеми полями (url, title, artist, coverUrl)."""
    stations = _load()
    q = _norm(query)
    if not q:
        return None
    # точное совпадение сначала
    for name, info in stations.items():
        if _norm(name) == q:
            return {"name": name, **info}
    # потом по подстроке
    for name, info in stations.items():
        n = _norm(name)
        if q in n or n in q:
            return {"name": name, **info}
    return None


def all_names() -> list[str]:
    return list(_load().keys())

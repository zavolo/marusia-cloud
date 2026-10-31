"""Хранилище будильников per device_id. Формат точно как у боевого mail.ru:
  {id, revision, enabled, alarm_time (sec from midnight), weekdays, first_time}

Пишем в JSON-файл чтобы переживало рестарт контейнера. Атомарная запись.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

_STORE_PATH = Path(os.environ.get("MARUSIA_ALARMS_PATH", "/data/alarms.json"))
_data: dict[str, list[dict]] = {}
_loaded = False


def _load() -> None:
    global _data, _loaded
    if _loaded:
        return
    try:
        _data = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        _data = {}
    _loaded = True


def _flush() -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_STORE_PATH)


def list_for(device_id: str) -> list[dict]:
    _load()
    return _data.get(device_id, [])


def set_alarm(device_id: str, alarm_time_sec: int, weekdays: list[int] | None = None,
              enabled: bool = True) -> dict:
    """Добавляет будильник. alarm_time_sec — секунды от полуночи локальной tz.
    first_time — unix TS ближайшего срабатывания (сегодня или завтра)."""
    _load()
    now_local = time.time()
    today_midnight = now_local - (now_local % 86400) - time.timezone  # приблизительно
    first = today_midnight + alarm_time_sec
    if first < now_local:
        first += 86400
    rev = max((a.get("revision", 0) for a in _data.get(device_id, [])), default=0) + 1
    alarm = {
        "id": str(uuid.uuid4()),
        "revision": rev,
        "enabled": enabled,
        "alarm_time": alarm_time_sec,
        "weekdays": weekdays or [],
        "first_time": int(first),
    }
    _data.setdefault(device_id, []).insert(0, alarm)
    _flush()
    return alarm


def delete_by_time(device_id: str, alarm_time_sec: int) -> int:
    """Выключает (enabled=false) все будильники на указанное время. Возвращает количество."""
    _load()
    n = 0
    for a in _data.get(device_id, []):
        if a.get("alarm_time") == alarm_time_sec and a.get("enabled"):
            a["enabled"] = False
            a["revision"] = max((x.get("revision", 0) for x in _data[device_id]), default=0) + 1
            n += 1
    if n:
        _flush()
    return n


def delete_all(device_id: str) -> int:
    _load()
    n = 0
    for a in _data.get(device_id, []):
        if a.get("enabled"):
            a["enabled"] = False
            n += 1
    if n:
        rev = max((x.get("revision", 0) for x in _data[device_id]), default=0) + 1
        for a in _data[device_id]:
            a["revision"] = rev
            rev += 1
        _flush()
    return n


# ---- парсер «на HH:MM» / «на HH часов MM минут» / «на HH» -------------------
_NUM_RU = {
    "ноль": 0, "один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19, "двадцать": 20, "тридцать": 30, "сорок": 40,
    "пятьдесят": 50, "шестьдесят": 60,
}


def _words_to_num(words: list[str]) -> int | None:
    total, buf = 0, 0
    found = False
    for w in words:
        if w in _NUM_RU:
            buf += _NUM_RU[w]
            found = True
        else:
            total += buf
            buf = 0
    total += buf
    return total if found else None


def parse_time(text: str) -> int | None:
    """Распознаёт в тексте время, возвращает секунды от полуночи.
    Форматы: «7:30», «семь тридцать», «19 48», «двадцать сорок восемь»."""
    t = text.lower()
    # 1) явный HH:MM
    m = re.search(r"(\d{1,2})[:\.\s](\d{2})", t)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        if 0 <= h < 24 and 0 <= mn < 60:
            return h * 3600 + mn * 60
    # 2) собираем все числовые токены (цифры + русские числительные)
    tokens = re.findall(r"\d+|[а-яё]+", t)
    nums: list[int] = []  # список чисел-слов 1:1 (двадцать=20, сорок=40, восемь=8)
    for w in tokens:
        if w.isdigit():
            nums.append(int(w))
        elif w in _NUM_RU:
            nums.append(_NUM_RU[w])
    if not nums:
        return None
    # 3) берём первую возможную HH:MM-разбивку
    for split in range(1, len(nums) + 1):
        h = sum(nums[:split])
        m = sum(nums[split:])
        if 0 <= h < 24 and 0 <= m < 60:
            return h * 3600 + m * 60
    # 4) одно число ≤ 23 — только часы
    if 0 <= nums[0] < 24:
        return nums[0] * 3600
    return None


def format_time(sec: int) -> str:
    h, m = divmod(sec, 3600)
    m //= 60
    return f"{h:02d}:{m:02d}"

from __future__ import annotations

from datetime import datetime, timezone

from . import db
from .models import DeviceSetting


def _merge(default, stored):
    if isinstance(default, dict) and isinstance(stored, dict):
        out = dict(default)
        out.update(stored)
        return out
    return stored if stored is not None else default


class PgSettingStore:
    async def get(self, device_id: str, key: str, default=None):
        async with db.sessionmaker()() as s:
            row = await s.get(DeviceSetting, (device_id, key))
            return _merge(default, row.value if row else None)

    async def put(self, device_id: str, key: str, value: dict) -> int:
        now = datetime.now(timezone.utc)
        async with db.sessionmaker()() as s, s.begin():
            row = await s.get(DeviceSetting, (device_id, key))
            if row is None:
                s.add(DeviceSetting(device_id=device_id, key=key, value=value,
                                    revision=1, updated_at=now))
                return 1
            row.value = value
            row.revision = (row.revision or 0) + 1
            row.updated_at = now
            return row.revision


class MemorySettingStore:
    def __init__(self):
        self._d: dict[tuple[str, str], dict] = {}

    async def get(self, device_id: str, key: str, default=None):
        return _merge(default, self._d.get((device_id, key)))

    async def put(self, device_id: str, key: str, value: dict) -> int:
        self._d[(device_id, key)] = value
        return 1

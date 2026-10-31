from __future__ import annotations

import time
import uuid

from sqlalchemy import select, update

from .. import alarms as _mem
from ..alarms import format_time, parse_time
from . import db
from .models import Alarm


class MemoryAlarmStore:

    async def list_for(self, device_id: str) -> list[dict]:
        return _mem.list_for(device_id)

    async def set_alarm(self, device_id: str, alarm_time_sec: int,
                        weekdays: list[int] | None = None, enabled: bool = True) -> dict:
        return _mem.set_alarm(device_id, alarm_time_sec, weekdays, enabled)

    async def delete_by_time(self, device_id: str, alarm_time_sec: int) -> int:
        return _mem.delete_by_time(device_id, alarm_time_sec)

    async def delete_all(self, device_id: str) -> int:
        return _mem.delete_all(device_id)

    async def set_music(self, alarm_id: str, music_id: str) -> None:
        pass


def _to_dict(a: Alarm) -> dict:
    return {
        "id": str(a.id),
        "revision": a.revision,
        "enabled": a.enabled,
        "alarm_time": a.alarm_time,
        "weekdays": list(a.weekdays or []),
        "first_time": a.first_time,
        "music_id": a.music_id,
    }


def _next_fire(alarm_time_sec: int) -> int:
    now_local = time.time()
    today_midnight = now_local - (now_local % 86400) - time.timezone
    first = today_midnight + alarm_time_sec
    if first < now_local:
        first += 86400
    return int(first)


class PgAlarmStore:
    async def list_for(self, device_id: str) -> list[dict]:
        async with db.sessionmaker()() as s:
            rows = (await s.scalars(
                select(Alarm).where(Alarm.device_id == device_id)
                .order_by(Alarm.created_at.desc()))).all()
            return [_to_dict(a) for a in rows]

    async def set_alarm(self, device_id: str, alarm_time_sec: int,
                        weekdays: list[int] | None = None,
                        enabled: bool = True) -> dict:
        async with db.sessionmaker()() as s, s.begin():
            max_rev = await s.scalar(
                select(Alarm.revision).where(Alarm.device_id == device_id)
                .order_by(Alarm.revision.desc()).limit(1))
            a = Alarm(
                id=uuid.uuid4(), device_id=device_id, revision=(max_rev or 0) + 1,
                enabled=enabled, alarm_time=alarm_time_sec,
                weekdays=weekdays or [], first_time=_next_fire(alarm_time_sec))
            s.add(a)
            await s.flush()
            return _to_dict(a)

    async def delete_by_time(self, device_id: str, alarm_time_sec: int) -> int:
        async with db.sessionmaker()() as s, s.begin():
            rows = (await s.scalars(
                select(Alarm).where(Alarm.device_id == device_id,
                                    Alarm.alarm_time == alarm_time_sec,
                                    Alarm.enabled.is_(True)))).all()
            max_rev = await s.scalar(
                select(Alarm.revision).where(Alarm.device_id == device_id)
                .order_by(Alarm.revision.desc()).limit(1)) or 0
            for a in rows:
                a.enabled = False
                max_rev += 1
                a.revision = max_rev
            return len(rows)

    async def delete_all(self, device_id: str) -> int:
        async with db.sessionmaker()() as s, s.begin():
            rows = (await s.scalars(
                select(Alarm).where(Alarm.device_id == device_id,
                                    Alarm.enabled.is_(True)))).all()
            max_rev = await s.scalar(
                select(Alarm.revision).where(Alarm.device_id == device_id)
                .order_by(Alarm.revision.desc()).limit(1)) or 0
            for a in rows:
                a.enabled = False
                max_rev += 1
                a.revision = max_rev
            return len(rows)

    async def set_music(self, alarm_id: str, music_id: str) -> None:
        async with db.sessionmaker()() as s, s.begin():
            await s.execute(update(Alarm).where(Alarm.id == uuid.UUID(alarm_id))
                            .values(music_id=music_id))

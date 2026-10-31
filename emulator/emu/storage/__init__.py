from __future__ import annotations

import logging

from . import db, rds

log = logging.getLogger("emu.storage")


async def init_all(app=None) -> None:
    await db.init(app)
    await rds.init(app)


async def dispose_all(app=None) -> None:
    await rds.dispose(app)
    await db.dispose(app)


def durable_enabled() -> bool:
    return db.enabled()


def make_stores(cfg):
    from .alarms import MemoryAlarmStore, PgAlarmStore
    from .cmdq import MemoryCommandStore, RedisCommandStore
    from .devices import MemoryDeviceStore, PgDeviceStore
    from .settings import MemorySettingStore, PgSettingStore
    from .users import MemoryUserStore, PgUserStore

    if db.enabled():
        device_store = PgDeviceStore(cfg.coordinates, cfg.strict_auth)
        alarm_store = PgAlarmStore()
        setting_store = PgSettingStore()
        user_store = PgUserStore()
        log.info("stores: Postgres (devices, alarms, settings, users)")
    else:
        device_store = MemoryDeviceStore(cfg.coordinates, cfg.strict_auth)
        alarm_store = MemoryAlarmStore()
        setting_store = MemorySettingStore()
        user_store = MemoryUserStore()
        log.info("stores: in-memory (devices, alarms, settings, users)")

    if rds.enabled():
        command_store = RedisCommandStore()
        log.info("stores: Redis (cmdq)")
    else:
        command_store = MemoryCommandStore()
        log.info("stores: in-memory (cmdq)")

    return device_store, alarm_store, command_store, setting_store, user_store

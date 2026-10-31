from __future__ import annotations

import logging
import os

from sqlalchemy.ext.asyncio import (
    AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine,
)

log = logging.getLogger("emu.db")

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def dsn() -> str | None:
    return os.environ.get("MARUSIA_DATABASE_URL") or None


def enabled() -> bool:
    return bool(dsn())


async def init(app=None) -> None:
    global _engine, _sessionmaker
    url = dsn()
    if not url:
        log.info("db disabled (no MARUSIA_DATABASE_URL) — in-memory mode")
        return
    if _engine is not None:
        return
    _engine = create_async_engine(
        url,
        pool_size=int(os.environ.get("MARUSIA_DB_POOL_SIZE", "5")),
        max_overflow=int(os.environ.get("MARUSIA_DB_MAX_OVERFLOW", "5")),
        pool_pre_ping=True,
        pool_recycle=300,
        echo=os.environ.get("MARUSIA_DB_ECHO") == "1",
    )
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    log.info("db engine ready: %s", url.split("@")[-1])


async def dispose(app=None) -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
        log.info("db engine disposed")


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("db not initialized (call storage.db.init first)")
    return _sessionmaker


async def ping() -> bool:
    if _sessionmaker is None:
        return False
    from sqlalchemy import text
    try:
        async with _sessionmaker() as s:
            await s.execute(text("SELECT 1"))
        return True
    except Exception as e:
        log.warning("db ping failed: %s", e)
        return False

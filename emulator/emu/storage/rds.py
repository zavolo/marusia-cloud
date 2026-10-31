from __future__ import annotations

import logging
import os

import redis.asyncio as redis

log = logging.getLogger("emu.redis")

_client: redis.Redis | None = None


def url() -> str | None:
    return os.environ.get("MARUSIA_REDIS_URL") or None


def enabled() -> bool:
    return bool(url())


async def init(app=None) -> None:
    global _client
    u = url()
    if not u:
        log.info("redis disabled (no MARUSIA_REDIS_URL)")
        return
    if _client is not None:
        return
    _client = redis.from_url(u, decode_responses=True,
                             health_check_interval=30)
    await _client.ping()
    log.info("redis ready: %s", u)


async def dispose(app=None) -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        log.info("redis closed")


def client() -> redis.Redis:
    if _client is None:
        raise RuntimeError("redis not initialized (call storage.rds.init first)")
    return _client


async def ping() -> bool:
    if _client is None:
        return False
    try:
        return bool(await _client.ping())
    except Exception as e:
        log.warning("redis ping failed: %s", e)
        return False

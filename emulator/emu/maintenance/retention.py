from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select, text

from ..storage import db
from ..storage.models import TtsCache

log = logging.getLogger("emu.retention")


def _int(env: str, default: int) -> int:
    try:
        return int(os.environ.get(env, "") or default)
    except ValueError:
        return default


async def prune_event_log() -> int:
    days = _int("MARUSIA_EVENTLOG_RETENTION_DAYS", 14)
    batch = _int("MARUSIA_RETENTION_BATCH", 5000)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = text(
        "DELETE FROM event_log WHERE id IN "
        "(SELECT id FROM event_log WHERE created_at < :cutoff ORDER BY id LIMIT :batch)")
    total = 0
    while True:
        async with db.sessionmaker()() as s, s.begin():
            res = await s.execute(stmt, {"cutoff": cutoff, "batch": batch})
            n = res.rowcount or 0
        total += n
        if n < batch:
            break
    if total:
        log.info("event_log: удалено %d строк старше %d дн.", total, days)
    return total


async def prune_tts_cache() -> int:
    max_bytes = _int("MARUSIA_TTSCACHE_MAX_BYTES", 512 * 1024 * 1024)
    deleted = 0
    async with db.sessionmaker()() as s, s.begin():
        total = int(await s.scalar(select(func.coalesce(func.sum(TtsCache.size_bytes), 0))) or 0)
        if total <= max_bytes:
            return 0
        rows = (await s.scalars(
            select(TtsCache).order_by(
                func.coalesce(TtsCache.last_hit_at, TtsCache.created_at).asc()))).all()
        for r in rows:
            if total <= max_bytes:
                break
            try:
                Path(r.blob_path).unlink(missing_ok=True)
            except OSError as e:
                log.warning("tts_cache: не удалил файл %s: %s", r.blob_path, e)
            await s.delete(r)
            total -= (r.size_bytes or 0)
            deleted += 1
    if deleted:
        log.info("tts_cache: вытеснено %d записей (лимит %d B)", deleted, max_bytes)
    return deleted


async def run_once() -> None:
    if not db.enabled():
        log.info("retention: БД выключена, нечего чистить")
        return
    await db.init()
    try:
        await prune_event_log()
        await prune_tts_cache()
    finally:
        await db.dispose()


async def run_loop() -> None:
    interval = _int("MARUSIA_RETENTION_INTERVAL", 86400)
    if not db.enabled():
        log.info("retention loop: БД выключена, выходим")
        return
    await db.init()
    log.info("retention loop: интервал %d сек", interval)
    try:
        while True:
            try:
                await prune_event_log()
                await prune_tts_cache()
            except Exception:
                log.exception("retention: прогон упал, продолжаем по расписанию")
            await asyncio.sleep(interval)
    finally:
        await db.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    import sys
    if "--loop" in sys.argv:
        asyncio.run(run_loop())
    else:
        asyncio.run(run_once())


if __name__ == "__main__":
    main()

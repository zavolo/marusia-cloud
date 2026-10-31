from __future__ import annotations

import logging

from . import db
from .models import EventLog

log = logging.getLogger("emu.events")


async def log_event(kind: str, device_id: str | None = None,
                    phrase_id: str | None = None, **payload) -> None:
    if not db.enabled():
        return
    try:
        async with db.sessionmaker()() as s, s.begin():
            s.add(EventLog(kind=kind, device_id=device_id,
                           phrase_id=phrase_id, payload=payload or {}))
    except Exception as e:
        log.warning("event_log write failed (%s): %s", kind, e)

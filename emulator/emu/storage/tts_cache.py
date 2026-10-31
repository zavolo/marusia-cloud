from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import update

from . import db
from .models import TtsCache

log = logging.getLogger("emu.tts_cache")


def _dir() -> Path | None:
    d = os.environ.get("MARUSIA_TTS_BLOBS")
    return Path(d) if d else None


def enabled() -> bool:
    return db.enabled() and _dir() is not None


def key_for(text: str, encoder: str, voice: str) -> str:
    return hashlib.sha256(f"{text}|{encoder}|{voice}".encode("utf-8")).hexdigest()


async def get(key: str) -> tuple[bytes, str] | None:
    if not enabled():
        return None
    try:
        async with db.sessionmaker()() as s, s.begin():
            row = await s.get(TtsCache, key)
            if row is None:
                return None
            p = Path(row.blob_path)
            if not p.exists():
                return None
            await s.execute(update(TtsCache).where(TtsCache.key == key)
                            .values(hits=TtsCache.hits + 1,
                                    last_hit_at=datetime.now(timezone.utc)))
            return p.read_bytes(), row.content_type
    except Exception as e:
        log.warning("tts_cache get failed: %s", e)
        return None


async def put(key: str, text: str, encoder: str, voice: str,
              data: bytes, content_type: str) -> None:
    d = _dir()
    if not enabled() or d is None:
        return
    try:
        d.mkdir(parents=True, exist_ok=True)
        ext = "ogg" if "ogg" in content_type else "wav"
        path = d / f"{key}.{ext}"
        path.write_bytes(data)
        async with db.sessionmaker()() as s, s.begin():
            if await s.get(TtsCache, key) is None:
                s.add(TtsCache(key=key, text=text[:2000], encoder=encoder, voice=voice,
                               content_type=content_type, blob_path=str(path),
                               size_bytes=len(data)))
    except Exception as e:
        log.warning("tts_cache put failed: %s", e)

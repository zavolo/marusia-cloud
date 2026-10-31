from __future__ import annotations

import asyncio
import logging
import os

import aiohttp

from . import asr as _local

log = logging.getLogger("emu.asr")


def _url() -> str | None:
    return os.environ.get("MARUSIA_ASR_URL") or None


def available() -> bool:
    return bool(_url()) or _local.available()


async def recognize(chunks: list[bytes]) -> str:
    if not chunks:
        return ""
    url = _url()
    if url:
        buf = b"".join(chunks)
        if len(buf) < 1600:
            return ""
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.post(url, data=buf,
                                  headers={"Content-Type": "application/octet-stream"}) as r:
                    r.raise_for_status()
                    j = await r.json()
                    return (j.get("text") or "").strip()
        except Exception as e:
            log.warning("remote asr failed (%s), fallback to local", e)
    return await asyncio.get_running_loop().run_in_executor(None, _local.recognize, chunks)

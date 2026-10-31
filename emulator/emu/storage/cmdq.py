from __future__ import annotations

import json
import time

from . import rds
from ..commands import CommandQueue

_MAX_HISTORY = 100


class MemoryCommandStore:

    def __init__(self):
        self._q = CommandQueue(max_history=_MAX_HISTORY)

    async def push(self, device_id: str, command: dict) -> dict:
        return self._q.push(device_id, command)

    async def drain(self, device_id: str) -> list[dict]:
        return self._q.drain(device_id)

    async def pending(self, device_id: str) -> int:
        return self._q.pending(device_id)

    async def history(self, device_id: str, limit: int = 50) -> list[dict]:
        return self._q.history(device_id, limit)


def _k_pending(dev: str) -> str: return f"cmdq:p:{dev}"
def _k_history(dev: str) -> str: return f"cmdq:h:{dev}"
def chan(dev: str) -> str: return f"cmdq:n:{dev}"


class RedisCommandStore:
    async def push(self, device_id: str, command: dict) -> dict:
        r = rds.client()
        seq = await r.incr("cmdq:seq")
        entry = {
            "id": seq,
            "type": command.get("type", "?"),
            "command": command,
            "created_at": time.time(),
            "status": "pending",
            "delivered_at": None,
        }
        blob = json.dumps(entry, ensure_ascii=False)
        async with r.pipeline(transaction=True) as pipe:
            pipe.rpush(_k_pending(device_id), blob)
            pipe.lpush(_k_history(device_id), blob)
            pipe.ltrim(_k_history(device_id), 0, _MAX_HISTORY - 1)
            await pipe.execute()
        await r.publish(chan(device_id), str(seq))
        return entry

    async def drain(self, device_id: str) -> list[dict]:
        r = rds.client()
        key = _k_pending(device_id)
        async with r.pipeline(transaction=True) as pipe:
            pipe.lrange(key, 0, -1)
            pipe.delete(key)
            items, _ = await pipe.execute()
        out = []
        now = time.time()
        for blob in items:
            try:
                entry = json.loads(blob)
            except (json.JSONDecodeError, TypeError):
                continue
            out.append(entry["command"])
        if items:
            await self._mark_delivered(device_id, now)
        return out

    async def _mark_delivered(self, device_id: str, ts: float) -> None:
        r = rds.client()
        hk = _k_history(device_id)
        raw = await r.lrange(hk, 0, _MAX_HISTORY - 1)
        changed = []
        for blob in raw:
            try:
                e = json.loads(blob)
            except (json.JSONDecodeError, TypeError):
                changed.append(blob)
                continue
            if e.get("status") == "pending":
                e["status"] = "delivered"
                e["delivered_at"] = ts
            changed.append(json.dumps(e, ensure_ascii=False))
        if changed:
            async with r.pipeline(transaction=True) as pipe:
                pipe.delete(hk)
                pipe.rpush(hk, *changed)
                await pipe.execute()

    async def pending(self, device_id: str) -> int:
        return int(await rds.client().llen(_k_pending(device_id)))

    async def history(self, device_id: str, limit: int = 50) -> list[dict]:
        raw = await rds.client().lrange(_k_history(device_id), 0, limit - 1)
        out = []
        for blob in raw:
            try:
                out.append(json.loads(blob))
            except (json.JSONDecodeError, TypeError):
                pass
        return out

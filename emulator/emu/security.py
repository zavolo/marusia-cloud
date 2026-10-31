from __future__ import annotations

import os
import time

from aiohttp import web

from .backend import api_error
from .storage import events, rds

_RL_EXEMPT_EXACT = {"/metrics", "/healthz", "/readyz", "/phrase/add",
                    "/connect", "/stream", "/purl"}
_RL_EXEMPT_PREFIX = ("/admin/",)

E_RATE_LIMITED = 4290


def _rl_config() -> tuple[int, int]:
    try:
        limit = int(os.environ.get("MARUSIA_RATE_LIMIT", "0") or 0)
        window = int(os.environ.get("MARUSIA_RATE_WINDOW", "60") or 60)
    except ValueError:
        return 0, 60
    return limit, window


async def _hit(ident: str, limit: int, window: int) -> tuple[bool, int]:
    r = rds.client()
    bucket = int(time.time()) // window
    key = f"rl:{ident}:{bucket}"
    n = await r.incr(key)
    if n == 1:
        await r.expire(key, window)
    return n <= limit, n


@web.middleware
async def rate_mw(request, handler):
    limit, window = _rl_config()
    if limit <= 0 or not rds.enabled():
        return await handler(request)
    p = request.path
    if p in _RL_EXEMPT_EXACT or any(p.startswith(x) for x in _RL_EXEMPT_PREFIX):
        return await handler(request)

    ident = request.query.get("device_id") or request.remote or "anon"
    allowed, n = await _hit(ident, limit, window)
    if not allowed:
        await events.log_event("audit", device_id=request.query.get("device_id"),
                               event="rate_limited", ident=ident[:64], path=p,
                               count=n, limit=limit, window=window)
        return api_error(request.get("qid", "-"), E_RATE_LIMITED,
                         "Too many requests", 429, request=request)
    return await handler(request)

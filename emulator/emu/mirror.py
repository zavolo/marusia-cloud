"""Прозрачный MITM-мидлварь: forward-ит запрос колонки на боевой vc.go.mail.ru,
записывает заголовки/тело запроса и ответа в журнал. Нужен для реверса схемы
ответов — чтобы наш эмулятор потом отдавал ровно те же поля.

Capability включается env `MARUSIA_MIRROR_UPSTREAM=https://vc.go.mail.ru`
(жёсткий форвард с SNI `MARUSIA_MIRROR_SNI`). А КОНКРЕТНО mirror
включается/выключается в РАНТАЙМЕ флагом в Redis — глобально или по device_id
(admin-API `/admin/mirror`). Так одну колонку можно пустить на зеркало боевого
сервера для дебага, остальные держать на эмуляторе; без редеплоя.

Флаги в Redis: `mirror:global`="1" (все) или `mirror:dev:<device_id>`="1" (одна).
Без Redis — фолбэк на старое поведение (upstream задан → зеркалим всё), как в
монолите. Лог — jsonl в `MARUSIA_MIRROR_LOG`, читается через `/admin/mirror/log`.
"""
from __future__ import annotations

import json
import os
import ssl
import time

import aiohttp
from aiohttp import web

from .storage import events, rds

_UPSTREAM = os.environ.get("MARUSIA_MIRROR_UPSTREAM", "")
_SNI = os.environ.get("MARUSIA_MIRROR_SNI", "vc.go.mail.ru")
_LOG = os.environ.get("MARUSIA_MIRROR_LOG", "/data/mirror.log")


async def mirror_active(request) -> bool:
    """Решает, зеркалить ли ЭТОТ запрос: рантайм-флаг в Redis (global / per-device).
    Без Redis — фолбэк на старое поведение (capability задана → зеркалим всё)."""
    if rds.enabled():
        try:
            r = rds.client()
            if await r.get("mirror:global") == "1":
                return True
            dev = request.query.get("device_id", "")
            if dev and await r.get(f"mirror:dev:{dev}") == "1":
                return True
            return False
        except Exception:  # noqa: BLE001
            return False
    return True  # нет Redis (монолит) — как раньше: upstream задан = зеркалим

# tls-клиент бьётся сразу в указанный IP, но cert проверяется под SNI vc.go.mail.ru
_sslctx = ssl.create_default_context()
_sslctx.check_hostname = True
_sslctx.verify_mode = ssl.CERT_REQUIRED

_sess: aiohttp.ClientSession | None = None


async def _session() -> aiohttp.ClientSession:
    global _sess
    if _sess is None or _sess.closed:
        _sess = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=_sslctx),
            headers={}, timeout=aiohttp.ClientTimeout(total=30),
        )
    return _sess


def _dump(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=repr)
    except Exception:  # noqa: BLE001
        return repr(obj)


def _append_log(line: str) -> None:
    try:
        d = os.path.dirname(_LOG)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


@web.middleware
async def mirror_mw(request, handler):
    # /admin/*  — наши админ endpoint-ы.
    # /purl     — наш прокси HTTP→HTTPS (mail.ru-sign мы не умеем).
    # /stream   — свой кэш если stream_id наш (petrovich-sfx), иначе через MITM.
    if request.path.startswith("/admin/") or request.path == "/purl":
        return await handler(request)
    if request.path == "/stream":
        sid = request.query.get("stream_id", "")
        if sid in request.app.get("streams", {}):
            return await handler(request)
        # иначе падаем в MITM-форвард ниже
    if not _UPSTREAM:
        return await handler(request)
    # рантайм-гейт: зеркалим только если включён флаг (глобально/для девайса)
    if not await mirror_active(request):
        return await handler(request)

    body = await request.read()
    url = f"{_UPSTREAM.rstrip('/')}{request.path_qs}"
    fwd = {k: v for k, v in request.headers.items()
           if k.lower() not in ("host", "connection", "content-length",
                                "transfer-encoding")}
    fwd["Host"] = _SNI

    t0 = time.time()
    try:
        s = await _session()
        async with s.request(request.method, url, headers=fwd, data=body,
                             allow_redirects=False, server_hostname=_SNI) as rr:
            resp_body = await rr.read()
            resp_headers = dict(rr.headers)
            resp_status = rr.status
    except Exception as e:  # noqa: BLE001
        _append_log(f"[{time.strftime('%H:%M:%S')}] UPSTREAM FAIL {request.method} {url} {e}")
        return await handler(request)

    dt = int((time.time() - t0) * 1000)
    rec = {
        "ts": time.strftime("%H:%M:%S"),
        "method": request.method,
        "path": request.path_qs,
        "req_hdrs": {k: v for k, v in fwd.items() if k not in ("Authorization", "X-Vc-Device-Secret")},
        "req_bearer": fwd.get("Authorization", ""),
        "req_body_len": len(body),
        "req_body_head": body[:4096].hex() if body else "",
        "resp_status": resp_status,
        "resp_hdrs": {k: v for k, v in resp_headers.items() if k not in ("Set-Cookie",)},
        "resp_body_len": len(resp_body),
        "resp_body_head": resp_body[:65536].hex() if resp_body else "",
        "upstream_ms": dt,
    }
    _append_log(_dump(rec))
    # компактная запись в event_log для queryability (без тел — они в файле)
    await events.log_event("mirror", device_id=request.query.get("device_id"),
                           path=request.path, status=resp_status, ms=dt,
                           req_len=len(body), resp_len=len(resp_body))

    # отдаём ответ без изменений (кроме hop-by-hop)
    out_hdrs = {k: v for k, v in resp_headers.items()
                if k.lower() not in ("transfer-encoding", "connection", "content-length",
                                     "content-encoding")}
    return web.Response(body=resp_body, status=resp_status, headers=out_hdrs)

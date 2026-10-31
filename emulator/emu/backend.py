"""Мимикрия бэкенда vc.go.mail.ru (nginx + JSON-конверт ошибок).

Реальный бэк (снято с https://vc.go.mail.ru):
  Server: nginx, на каждом ответе X-Request-Id, Access-Control-Max-Age, Vary: Origin
  404          -> text/plain "404: Not Found"
  ошибка       -> {"qid","error":"CODE:msg","code":4002,"reason":"msg"} + reason в статус-строке
  успех        -> {"result": {...}}
"""
from __future__ import annotations

import json
import logging
import secrets

import msgpack
from aiohttp import web

log = logging.getLogger("emu")

NGINX_HEADERS = {
    "Server": "nginx",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
}

# коды ошибок как у бэка (4002 = bad param; остальные — наши разумные)
E_BAD_PARAM = 4002
E_UNAUTHORIZED = 4010
E_NOT_FOUND = 4040
E_INTERNAL = 5000
E_SESSION_INVALID = 4030
E_UNKNOWN_DEVICE = 4007
E_XTOKEN_INVALID = 4027


def qid() -> str:
    return secrets.token_hex(16)


def _pack(body: dict) -> tuple[bytes, str]:
    """Выбор формата кузова по пути: тело для прошивки — msgpack, остальным — JSON.

    use_bin_type=False — это «старый» msgpack: строки идут как raw-type (0xa0+),
    без различия str/bin. Прошивка BES2300, судя по rt_logs/send и ошибкам
    парсинга, работает именно с этим вариантом.
    """
    return msgpack.packb(body, use_bin_type=False), "application/x-msgpack"


_MP_PATHS = ("/rt_logs/", "/data/")


def _wants_msgpack(request) -> bool:
    # Прошивка не шлёт Accept, но стучит исключительно в известные пути.
    p = getattr(request, "path", "")
    return any(p == x or p.startswith(x) for x in _MP_PATHS)


def ok(result: dict, status: int = 200, request=None) -> web.Response:
    """Успешный ответ.

    Marusia-протокол: {"result": "ok", <поля>} на верхнем уровне, msgpack.
    Для admin-API оставляем старую JSON-обёртку, т.к. он для curl'ов.
    """
    if request is not None and _wants_msgpack(request):
        body = {"result": "ok", **(result or {})}
        data, ctype = _pack(body)
        return web.Response(body=data, status=status, content_type=ctype)
    return web.json_response({"result": result}, status=status)


def _reason_phrase(message: str) -> str:
    """HTTP reason-строка не может содержать \\r\\n (aiohttp кидает ValueError).
    Берём первую строку и обрезаем — тело ответа несёт полное сообщение."""
    return message.splitlines()[0][:200] if message else "error"


def api_error(qid_: str, code: int, message: str, status: int = 400,
              request=None) -> web.Response:
    """Ошибка в формате бэка + кастомный reason-phrase в статус-строке."""
    body = {"qid": qid_, "error": f"{code}:{message}", "code": code, "reason": message}
    reason = _reason_phrase(message)
    if request is not None and _wants_msgpack(request):
        data, ctype = _pack(body)
        return web.Response(body=data, status=status, reason=reason,
                            content_type=ctype)
    return web.json_response(body, status=status, reason=reason)


def not_found() -> web.Response:
    return web.Response(status=404, reason="Not Found", text="404: Not Found",
                        content_type="text/plain", charset="utf-8")


def require_device_id(request) -> str:
    """device_id обязателен на большинстве эндпоинтов (иначе 400/4002, как у бэка)."""
    dev = request.query.get("device_id", "")
    if not dev:
        raise _ApiError(E_BAD_PARAM, "Bad param: device_id is required", 400)
    return dev


class _ApiError(Exception):
    def __init__(self, code: int, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status


_DUMP_HEADERS = {
    "host", "user-agent", "content-type", "content-length", "authorization",
    "upgrade", "connection", "sec-websocket-version", "sec-websocket-key",
    "sec-websocket-protocol", "sec-websocket-extensions",
    "x-vc-device-secret", "x-voice-client-time", "x-super-token",
    "x-csrf-protection",
}


@web.middleware
async def backend_mw(request, handler):
    """Единый конверт: X-Request-Id, nginx-заголовки, ошибки, логирование."""
    request["qid"] = qid()
    dev = (request.query.get("device_id", "") or "-")[-12:]

    # распаковка входящего msgpack тела — чтоб лог видел структуру, а не байты.
    # (хэндлеры тоже могут читать request._dec_body вместо json.loads.)
    # дамп входящего запроса — чтоб видеть, что реально шлёт колонка.
    # Тело читаем сразу (повторный await request.read() у aiohttp отдаёт тот
    # же буфер — handler ничего не теряет). Для /phrase/add и крупных тел
    # печатаем только размер.
    body = b""
    try:
        if request.can_read_body:
            body = await request.read()
    except Exception:
        pass
    hdrs = {k: v for k, v in request.headers.items() if k.lower() in _DUMP_HEADERS}
    big = request.path.startswith("/phrase/add") or len(body) > 512
    body_repr = f"<{len(body)}B>" if big else body
    log.info(">>>> %s %s qs=%s hdrs=%s body=%r",
             request.method, request.path_qs, dict(request.query), hdrs, body_repr)

    try:
        resp = await handler(request)
    except _ApiError as e:
        resp = api_error(request["qid"], e.code, e.message, e.status, request=request)
    except web.HTTPNotFound:
        resp = not_found()
    except web.HTTPException as e:
        resp = api_error(request["qid"], E_INTERNAL, e.reason or "error", e.status,
                         request=request)
    except Exception as e:  # noqa: BLE001
        log.exception("handler error")
        resp = api_error(request["qid"], E_INTERNAL, f"internal: {e}", 500,
                         request=request)

    for k, v in NGINX_HEADERS.items():
        resp.headers.setdefault(k, v)
    resp.headers["X-Request-Id"] = request["qid"]

    auth = request.get("auth_state", "-")
    log.info("%-4s %-30s dev=%s auth=%-7s -> %s",
             request.method, request.path, dev, auth, resp.status)
    return resp

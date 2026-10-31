from __future__ import annotations

import time

from aiohttp import web
from prometheus_client import (
    CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest,
)

REQUESTS = Counter(
    "marusia_requests_total", "HTTP-запросы к gateway",
    ["method", "path", "status"])
LATENCY = Histogram(
    "marusia_request_seconds", "Латентность обработки запроса",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30))
WS_ACTIVE = Gauge("marusia_ws_active", "Активные /connect WebSocket-соединения")
ASR_SECONDS = Histogram(
    "marusia_asr_seconds", "Время распознавания в asr-worker",
    buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 16))
TTS_SECONDS = Histogram(
    "marusia_tts_seconds", "Время синтеза в tts-worker",
    buckets=(0.5, 1, 2, 4, 8, 12, 20, 40))

_NORMALIZE = (
    "/phrase/create", "/phrase/add", "/phrase/result", "/phrase/commands",
    "/account/settings", "/device/settings", "/device/speedtest", "/data/",
    "/admin/",
)


def _norm_path(path: str) -> str:
    for p in _NORMALIZE:
        if path.startswith(p):
            return p.rstrip("/") or p
    return path


@web.middleware
async def metrics_mw(request, handler):
    if request.path in ("/metrics", "/healthz", "/readyz"):
        return await handler(request)
    t0 = time.perf_counter()
    status = 500
    try:
        resp = await handler(request)
        status = resp.status
        return resp
    finally:
        path = _norm_path(request.path)
        LATENCY.labels(request.method, path).observe(time.perf_counter() - t0)
        REQUESTS.labels(request.method, path, str(status)).inc()


async def metrics_handler(_request) -> web.Response:
    return web.Response(body=generate_latest(), content_type=CONTENT_TYPE_LATEST.split(";")[0])

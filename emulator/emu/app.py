"""Сборка aiohttp-приложения: состояние, middleware, маршруты."""
from __future__ import annotations

import logging

from aiohttp import web

from . import handlers as H
from . import storage
from .backend import backend_mw, _ApiError
from .config import Config
from .security import rate_mw
from .tts import TTSEngine

log = logging.getLogger("emu")


@web.middleware
async def auth_mw(request, handler):
    """Достаёт устройство/сессию из auth-заголовков, кладёт в request."""
    reg = request.app["reg"]
    dev, bearer = await reg.touch(request)
    request["device"] = dev
    request["bearer"] = bearer
    request["auth_state"] = reg.state(dev, bearer)
    return await handler(request)


import os as _os
from .mirror import mirror_mw
from . import storage
from .obs import metrics as obs_metrics


@web.middleware
async def infra_mw(request, handler):
    """Инфра-эндпоинты обслуживаем ПЕРВЫМИ, до mirror/auth/backend:
    /metrics (Prometheus), /healthz (liveness), /readyz (readiness — пинг пулов)."""
    p = request.path
    if p == "/metrics":
        return await obs_metrics.metrics_handler(request)
    if p == "/healthz":
        return web.json_response({"ok": True})
    if p == "/readyz":
        from .storage import db as _db, rds as _rds
        db_ok = (await _db.ping()) if _db.enabled() else True
        rds_ok = (await _rds.ping()) if _rds.enabled() else True
        ready = db_ok and rds_ok
        return web.json_response({"ready": ready, "db": db_ok, "redis": rds_ok},
                                 status=200 if ready else 503)
    return await handler(request)


@web.middleware
async def admin_guard_mw(request, handler):
    """Токен-гард на /admin/* (defense-in-depth; публично /admin закрыт reverse-proxy).
    Если MARUSIA_ADMIN_TOKEN задан — требуем X-Admin-Token. Иначе пропускаем."""
    if request.path.startswith("/admin"):
        tok = _os.environ.get("MARUSIA_ADMIN_TOKEN", "")
        if tok:
            got = request.headers.get("X-Admin-Token", "") or request.query.get("admin_token", "")
            if got != tok:
                raise _ApiError(4030, "admin token required", 403)
    return await handler(request)


async def _storage_init(app):
    await storage.init_all(app)


async def _skills_init(app):
    from .skills import catalog as skill_catalog
    await skill_catalog.init(app)


async def _storage_dispose(app):
    await storage.dispose_all(app)


def make_app(cfg: Config) -> web.Application:
    mws = [obs_metrics.metrics_mw, backend_mw, admin_guard_mw, rate_mw, auth_mw]
    if _os.environ.get("MARUSIA_MIRROR_UPSTREAM"):
        # прозрачный MITM: все HTTP-запросы колонки на /phrase/*, /account/*,
        # /device/*, /rt_logs/*, /data/* летят в боевой vc.go.mail.ru,
        # записываем в журнал, отдаём ответ как есть.
        mws = [mirror_mw] + mws
    mws = [infra_mw] + mws          # инфра — строго первым слоем
    app = web.Application(middlewares=mws,
                          client_max_size=32 * 1024 * 1024)
    app.on_startup.append(_storage_init)
    app.on_startup.append(_skills_init)
    app.on_cleanup.append(_storage_dispose)
    app["cfg"] = cfg
    device_store, alarm_store, command_store, setting_store, user_store = storage.make_stores(cfg)
    app["reg"] = device_store
    app["alarms"] = alarm_store
    app["cmdq"] = command_store
    app["settings"] = setting_store
    app["users"] = user_store
    app["tts"] = TTSEngine(cfg)
    app["xtokens"] = {}    # xtoken -> account_id (эфемерно, как у боевого create_xtoken)
    app["sessions"] = {}   # phrase_id -> session
    app["dialogs"] = {}    # device_id -> {"skill":..., "state":{...}} активный диалог
    app["streams"] = {}    # stream_id -> (bytes, content_type)
    # Предзагружаем треки-пасхалки: ключ фиксирован, URL/stream_id не меняется.
    for sid, path in {
        "petrovich-sfx": "/app/static/petrovich.opus",
    }.items():
        try:
            with open(path, "rb") as f:
                app["streams"][sid] = (f.read(), "audio/ogg")
                log.info("preloaded stream %s <- %s (%dB)", sid, path, len(app["streams"][sid][0]))
        except FileNotFoundError:
            log.info("preload skipped: %s not found", path)
    app["ws_devices"] = {} # device_id -> WebSocketResponse (живой /connect)
    app["live_subs"] = {}  # device_id -> list[asyncio.Queue] (подписчики live-аудио)

    r = app.router
    # phrase-протокол
    for suffix in ("", "/0chunk", "/text", "/event", "/flowmode"):
        r.add_post(f"/phrase/create{suffix}", H.phrase_create)
    r.add_post("/phrase/add", H.phrase_add)
    r.add_route("*", "/phrase/result", H.phrase_result)
    r.add_route("*", "/phrase/commands", H.phrase_commands)
    r.add_get("/stream", H.stream)
    r.add_get("/purl", H.purl)
    # аккаунт
    r.add_post("/account/attach_xtoken", H.account_attach)
    r.add_post("/account/create_xtoken", H.account_create_xtoken)
    r.add_get("/account/devices", H.account_devices)
    r.add_get("/account/info", H.account_info)
    r.add_get("/account/vk/info", H.account_info)
    r.add_route("*", "/account/vk/upgrade_token", H.account_vk_upgrade_token)
    r.add_get("/account/settings/v1/reminders", H.account_reminders)
    # устройство / телеметрия
    r.add_route("*", "/device/settings/v1/{tail:.*}", H.device_settings)
    r.add_post("/device/stat", H.device_stat)
    r.add_route("*", "/device/toggles", H.device_toggles)
    r.add_route("*", "/device/tg_auth/mobile_send", H.data_sink)
    r.add_route("*", "/device/calls/vk/hang_up", H.data_sink)
    r.add_post("/rt_logs/send", H.rt_logs)
    r.add_get("/device/timezone/info", H.device_timezone)
    r.add_get("/device/firmware", H.device_firmware)
    # speedtest-ответы: прошивка пишет json с latency_ms/upload_rate_kbps/download_rate_kbps
    for path, h in (("/device/speedtest/download", H.speedtest_dl),
                    ("/device/speedtest/upload",   H.speedtest_up),
                    ("/device/speedtest/results",  H.speedtest_res)):
        r.add_route("*", path, h)
    # crash/abort — принимаем «в молоко» (прошивка просто уходит в них, если крашилась)
    for path in ("/data/crash_reports",
                 "/data/crash_reports/crash_report",
                 "/data/crash_reports/log_file_index",
                 "/data/smartbox_abort"):
        r.add_route("*", path, H.data_sink)
    r.add_route("*", "/connect", H.connect)
    r.add_route("*", "/content_privacy", H.content_privacy)
    # admin-управление (наш пульт)
    r.add_post("/admin/say", H.admin_say)
    r.add_post("/admin/command", H.admin_command)
    r.add_post("/admin/flowmode", H.admin_flowmode)
    r.add_post("/admin/clock", H.admin_clock)
    r.add_get("/admin/history", H.admin_history)
    r.add_get("/admin/alarms", H.admin_alarms)
    r.add_get("/admin/events", H.admin_events)
    r.add_get("/admin/mirror/log", H.admin_mirror_log)
    r.add_route("*", "/admin/mirror", H.admin_mirror)
    r.add_get("/admin/devices", H.admin_devices)
    r.add_get("/admin/skills", H.admin_skills)
    r.add_post("/admin/skills", H.admin_skill_set)
    r.add_post("/admin/skills/responses", H.admin_skill_responses)
    r.add_post("/admin/skills/webhook", H.admin_skill_webhook)
    r.add_route("*", "/admin/skills/test", H.admin_skill_test)
    r.add_get("/admin/users", H.admin_users)
    r.add_post("/admin/users", H.admin_user_create)
    r.add_post("/admin/users/delete", H.admin_user_delete)
    r.add_post("/admin/users/password", H.admin_user_password)
    r.add_post("/admin/users/disable", H.admin_user_disable)
    r.add_post("/admin/bind", H.admin_bind)
    r.add_post("/admin/unbind", H.admin_unbind)
    r.add_post("/admin/pair/code", H.admin_pair_code)
    r.add_post("/admin/pair/redeem", H.admin_pair_redeem)
    r.add_get("/admin/flow-log", H.admin_flow_log)
    r.add_get("/admin/recordings", H.admin_recordings)
    r.add_get("/admin/live.wav", H.admin_live_wav)
    r.add_get("/admin/live", H.admin_live_page)
    # всё прочее -> 404 как у бэка (+ лог для доработки)
    r.add_route("*", "/{tail:.*}", _catch_all)

    # Прогрев TTS-кэша типовыми фразами: при CPU-RVC одна фраза ~12 сек,
    # колонка так долго не ждёт — лучше нагенерить заранее на старте.
    async def _warmup(_app):
        import asyncio
        asyncio.create_task(_app["tts"].warmup([
            "Да",
            "Привет, это Маруся.",
            cfg.reply_text,
            "Тест прошёл.",
            "Я вас слушаю.",
            "Повторите, пожалуйста.",
            "Петрович уехал на говновозе!",
            "О, Петрович вернулся.",
            "Открываю глазки",
            "Закрываю глазки",
        ]))
    app.on_startup.append(_warmup)
    return app


async def _catch_all(request):
    body = await request.read()
    log.info("   [UNHANDLED] %s %s q=%s body=%dB",
             request.method, request.path, dict(request.query), len(body))
    raise web.HTTPNotFound()

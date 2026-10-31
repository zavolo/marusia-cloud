"""HTTP-обработчики: phrase-протокол, аккаунт, устройство, стрим, admin-управление."""
from __future__ import annotations

import json
import logging
import asyncio
import os
import time

from aiohttp import web

from . import alarms, asr_client, commands as C, radio
from .backend import (E_UNAUTHORIZED, E_SESSION_INVALID, E_UNKNOWN_DEVICE, E_BAD_PARAM,
                      E_XTOKEN_INVALID, ok, require_device_id, _ApiError)
from .storage import events
from .obs.metrics import WS_ACTIVE
from .skills import catalog as skill_catalog, engine as skill_engine

log = logging.getLogger("emu")


# ---- вспомогательное --------------------------------------------------------
def _strict_guard(request):
    reg = request.app["reg"]
    if not reg.strict:
        return
    dev = request["device"]
    if not reg.valid_bearer(dev):
        # Прошивка на 401 просто пишет "связь" и не делает re-attach.
        # Пробуем 403 с "session_invalid" — надеемся что это триггер
        # setup_logic.c:_attach_cb → vc_api_account_attach_xtoken_create.
        raise _ApiError(E_SESSION_INVALID, "session_invalid", 403)


async def _make_tts_command(request, text: str) -> dict:
    """Синтез Opus + сохранение стрима + команда tts.

    Если задан cfg.static_audio — вместо TTS отдаём готовый Ogg Opus (на всю длину
    ответа); колонка декодирует и играет файл как озвучку.
    """
    cfg, streams, tts = request.app["cfg"], request.app["streams"], request.app["tts"]
    sid = C.new_stream_id()
    if cfg.static_audio:
        with open(cfg.static_audio, "rb") as f:
            streams[sid] = (f.read(), "audio/ogg")
    else:
        audio, ctype = await tts.synth(text, cfg.tts_encoder)
        streams[sid] = (audio, ctype)
    return C.tts(text, sid, encoder=cfg.tts_encoder)


# ---- phrase-протокол --------------------------------------------------------
async def phrase_create(request):
    """POST /phrase/create[/0chunk|/text|/event|/flowmode] — открыть фразу.

    Нео не запрашивает /phrase/result и игнорирует WS push. Пробуем отдать
    команды прямо в теле ответа на create/0chunk — вместе с phrase_id.
    """
    require_device_id(request)
    _strict_guard(request)
    q = dict(request.query)
    body = await request.read()
    sessions = request.app["sessions"]
    pid = q.get("phrase_id") or _new_phrase_id()
    # activation_type: 'kws' (wake-word) или 'listen_command' (продолжение диалога).
    # Для listen_command на тишину не отвечаем «Да» — иначе бесконечный цикл.
    act = q.get("activation_type", "kws")
    sessions[pid] = {"device_id": q.get("device_id", ""), "chunks": [], "nbytes": 0,
                     "statuses": [], "t0": time.time(),
                     "flowmode": request.path.endswith("flowmode"),
                     "activation_type": act}
    if body:
        sessions[pid]["chunks"].append(body)
        sessions[pid]["nbytes"] += len(body)
    log.info("   create phrase_id=%s (%dB initial, %s, act=%s)", pid, len(body),
             "flowmode" if sessions[pid]["flowmode"] else "kw", act)

    # mail.ru на /phrase/create[/0chunk] отвечает ровно так (snif-ed через mirror):
    # {"result":{"phrase_id":"YYYYMMDD-HHMM-xxxx-xxxxxxxxxxxx","status":1,"chunk_length_hint":80}}
    return ok({
        "phrase_id": pid,
        "status": 1,
        "chunk_length_hint": 80,
    }, request=request)


async def _push_phrase_result_ws(request, pid: str) -> None:
    """После короткой задержки шлёт команды в /connect WebSocket устройства."""
    import asyncio
    await asyncio.sleep(1.0)
    dev_id = request.app["sessions"].get(pid, {}).get("device_id", "")
    ws = request.app["ws_devices"].get(dev_id)
    if ws is None or ws.closed:
        log.info("   push WS: нет живого /connect для dev=%s pid=%s", dev_id[-12:], pid)
        return
    cfg = request.app["cfg"]
    commands = [
        C.text(cfg.reply_text),
        await _make_tts_command(request, cfg.reply_text),
        C.listen(),
    ]
    envelope = {
        "result": {
            "phrase_result": {
                "commands": commands,
                "controls": [],
                "asr_text": cfg.reply_text,
                "skill": "emulator",
                "intent": "emulator.reply",
            },
            "phrase_id": pid,
        },
    }
    import msgpack as _mp
    # Схема control-conn: внешний конверт {code, ts, data}, где `data` — это
    # bin (msgpack bin), содержащий вложенный msgpack с полезной нагрузкой.
    # Прошивка парсит внешний msgpack, видит `data` типа bin, проверяет его
    # длину (limit 51200) — у нас сильно меньше, значит проходит; потом
    # разворачивает внутренний msgpack как сообщение.
    inner = _mp.packb({
        "commands": commands,
        "phrase_id": pid,
    }, use_bin_type=True)
    control = {
        "code": 2000,
        "ts": time.time(),
        "data": inner,  # bytes -> msgpack bin type
    }
    payload = _mp.packb(control, use_bin_type=True)
    try:
        await ws.send_bytes(payload)
        log.info("   push WS --> dev=%s pid=%s outer=%dB inner=%dB",
                 dev_id[-12:], pid, len(payload), len(inner))
    except Exception as e:  # noqa: BLE001
        log.warning("   push WS fail dev=%s: %s", dev_id[-12:], e)


async def phrase_add(request):
    """POST /phrase/add — аудио-чанк. Во flowmode status всегда OKAY_GO_ON (2)."""
    require_device_id(request)
    q = dict(request.query)
    body = await request.read()
    pid = q.get("phrase_id", "")
    s = request.app["sessions"].get(pid)
    flowmode = bool(s and s.get("flowmode"))
    if s is not None:
        s["chunks"].append(body)
        s["nbytes"] += len(body)
        _broadcast_live(request.app, s.get("device_id", ""), body)
        if flowmode:
            await _flowmode_tick(request, s, body, pid)
        if q.get("status"):
            s["statuses"].append(q["status"])
    # mail.ru:
    #   status=2 — «продолжай писать» (OKAY_GO_ON);
    #   status=6 — «фраза распознана» (PHRASE_RECOGNIZED), колонка останавливает
    #              запись и идёт в /phrase/result.
    # ASR эмулятора нет — отдаём 6 после ~10 чанков (≈1.6с аудио) чтобы колонка
    # не стримила вечно.
    chunk = int(q.get("chunk_num", "0") or 0)
    if flowmode:
        status = 2  # OKAY_GO_ON навсегда — непрерывный стрим
    else:
        status = 6 if chunk >= 10 else 2
    return ok({"status": status, "chunk_length_hint": 80}, request=request)


def _broadcast_live(app, device_id: str, wav_body: bytes) -> None:
    """Рассылает PCM последнего чанка всем подписчикам /admin/live.wav."""
    try:
        import wave
        from io import BytesIO
        w = wave.open(BytesIO(wav_body), "rb")
        pcm = w.readframes(w.getnframes())
    except Exception:  # noqa: BLE001
        return
    subs_map = app.get("live_subs", {})
    for key, subs in list(subs_map.items()):
        if device_id and device_id not in key and key not in device_id:
            continue
        for q in list(subs):
            try:
                if q.qsize() > 50:
                    continue  # медленный клиент — пропускаем
                q.put_nowait(pcm)
            except Exception:  # noqa: BLE001
                pass


async def _flowmode_tick(request, s: dict, body: bytes, pid: str) -> None:
    """Пишем WAV на диск + периодическая Vosk-транскрипция."""
    import os as _os
    rec_dir = "/data/recordings"
    _os.makedirs(rec_dir, exist_ok=True)
    f = s.get("flow_pcm_path")
    if not f:
        dev_id = s.get("device_id", "").replace(":", "_")[-32:]
        f = f"{rec_dir}/{dev_id}_{int(time.time())}.pcm"
        s["flow_pcm_path"] = f
        s["flow_last_asr_chunk"] = 0
        log.info("   flowmode %s start -> %s", pid[:8], f)
    try:
        import wave
        from io import BytesIO
        w = wave.open(BytesIO(body), "rb")
        pcm = w.readframes(w.getnframes())
        with open(f, "ab") as fp:
            fp.write(pcm)
    except Exception:  # noqa: BLE001
        pass
    n = len(s["chunks"])
    if n - s["flow_last_asr_chunk"] >= 12 and asr_client.available():
        s["flow_last_asr_chunk"] = n
        text = await asr_client.recognize(s["chunks"][-24:])
        if text:
            log.info("   flowmode %s asr: %r", pid[:8], text)
            flog = request.app.setdefault("flow_log", [])
            flog.append({"ts": time.time(), "pid": pid, "text": text})


async def phrase_result(request):
    """GET|POST /phrase/result — ASR результат + page_token.

    Эталон (v3rt3p/marusya-server): команды НЕ сюда, а в /phrase/commands.
    Здесь только asr_text, skill, intent, page_token (под ним колонка пойдёт
    опрашивать /phrase/commands за tts/listen).
    """
    require_device_id(request)
    _strict_guard(request)
    cfg = request.app["cfg"]
    dev = request["device"]
    pid = request.query.get("phrase_id", "")
    s = request.app["sessions"].get(pid, {"chunks": [], "nbytes": 0, "statuses": []})

    # Распознаём речь через asr-worker (или локальный Vosk-фолбэк).
    asr_text = await asr_client.recognize(s["chunks"])
    ctx = skill_engine.Context(
        app=request.app, device_id=dev.device_id, asr_text=asr_text,
        norm=skill_catalog.normalize(asr_text),
        activation_type=s.get("activation_type", "kws"), pid=pid)
    reply = await skill_engine.resolve(ctx)
    # «Да» на тишину только по честному wake-word, не в follow-up listen
    # (`activation_type=listen_command`), иначе бесконечный цикл.
    is_followup = s.get("activation_type") == "listen_command"
    if reply is None and not asr_text and s["nbytes"] >= 30000 and not is_followup:
        reply = skill_engine.Reply(skill="greeting", intent="greeting.yes", say="Да", listen=True)

    s["reply_obj"] = reply
    s["asr_text"] = asr_text
    s["served"] = False
    s["should_listen"] = bool(reply and reply.listen)
    request.app["sessions"][pid] = s

    log.info("   result phrase_id=%s asr=%r skill=%r say=%r (%dB audio)",
             pid, asr_text, reply.skill if reply else None,
             reply.say if reply else None, s["nbytes"])
    await events.log_event("asr", device_id=dev.device_id, phrase_id=pid,
                           asr_text=asr_text, skill=reply.skill if reply else None,
                           nbytes=s["nbytes"])
    return ok({
        "commands": [],
        "controls": [],
        "skill": reply.skill if reply else None,
        "asr_text": asr_text or "",
        "secure": False,
        "intent": reply.intent if reply else "controls.pause",
        "page_token": f"{pid}/{C.uuid.uuid4()}",
    }, request=request)


async def phrase_commands(request):
    """GET|POST /phrase/commands — отдаём tts+listen по page_token.

    Эталон: колонка после /phrase/result идёт сюда, забирает команды пачками,
    пока не кончатся. Если ещё что-то есть — вернуть page_token; иначе — без него.
    """
    require_device_id(request)
    cfg, cmdq = request.app["cfg"], request.app["cmdq"]
    dev = request["device"]
    pid = request.query.get("phrase_id", "")
    if not pid:
        try:
            body = await request.json()
            tok = body.get("page_token", "")
            if "/" in tok:
                pid = tok.split("/", 1)[0]
        except Exception:  # noqa: BLE001
            pass

    s = request.app["sessions"].get(pid)
    reply = s.get("reply_obj") if s else None
    already_served = s.get("served") if s else True

    cmds = await cmdq.drain(dev.device_id)
    spoke = False
    if reply is not None:
        if reply.say and not already_served:
            cmds.insert(0, await _make_tts_command(request, reply.say))
            s["served"] = True
            spoke = True
        if reply.commands:
            cmds.extend(reply.commands)
        if reply.listen:
            cmds.append(C.listen(dialog_id=pid, min_waiting_time=4))

    log.info("   commands phrase_id=%s skill=%r spoke=%r listen=%r total=%d",
             pid, reply.skill if reply else None, spoke,
             bool(reply and reply.listen), len(cmds))
    return ok({"commands": cmds, "controls": []}, request=request)


_WS_MSG_ACK = 1
_WS_MSG_NAK = 2
_WS_MSG_UPDATE_CONFIG = 8
_WS_MSG_COMMAND_CONTROL = 10


async def _cmdq_listener(app, dev_full: str, ws) -> None:
    """Слушает Redis pub/sub канал cmdq устройства: когда кто-то (в т.ч. отдельная
    админка) кладёт команду в очередь и публикует — сливаем очередь и пушим в WS.
    Так разнесённая админка доставляет команды колонке в реальном времени."""
    from .storage import rds
    from .storage import cmdq as _cq
    if not rds.enabled():
        return
    psub = rds.client().pubsub()
    try:
        await psub.subscribe(_cq.chan(dev_full))
        async for msg in psub.listen():
            if msg.get("type") != "message":
                continue
            cmds = await app["cmdq"].drain(dev_full)
            if cmds:
                try:
                    await _ws_push_commands(ws, cmds)
                    log.info("   cmdq->WS push dev=%s cmds=%d", dev_full[-12:], len(cmds))
                except Exception as e:  # noqa: BLE001
                    log.warning("   cmdq->WS push fail dev=%s: %s", dev_full[-12:], e)
    except asyncio.CancelledError:
        pass
    except Exception as e:  # noqa: BLE001
        log.warning("   cmdq listener dev=%s died: %s", dev_full[-12:], e)
    finally:
        try:
            await psub.unsubscribe(_cq.chan(dev_full))
            await psub.aclose()
        except Exception:  # noqa: BLE001
            pass


async def _ws_push_commands(ws, cmds: list[dict]) -> None:
    """Шлёт команды в WS /connect в формате BES2300WP:
      header 32B BE: [type:u32][body_len:u32][zeros:8B][rand_id:16B] + JSON body
      type=10 COMMAND_CONTROL, body = {commands, controls, skill:"push"}
    """
    import secrets, struct
    body = json.dumps({"commands": cmds, "controls": [], "skill": "push"},
                      ensure_ascii=False).encode("utf-8")
    header = (struct.pack(">II", _WS_MSG_COMMAND_CONTROL, len(body))
              + b"\x00" * 8
              + secrets.token_bytes(16))
    await ws.send_bytes(header + body)


async def purl(request):
    """GET /purl?url=X&device_id=&ttl=&p=&sign=  — transparent HTTP→HTTPS proxy.

    Mail.ru так проксирует http-only радиостанции (Милицейская волна,
    Балтик Плюс и т.п.) чтобы колонка-TLS-only могла их принимать.
    Мы `sign` не проверяем — просто разворачиваем URL и стримим байты.
    Icy-headers Shoutcast пробрасываем, тип ответа `audio/mpeg`.
    """
    import aiohttp as _ah
    target = request.query.get("url", "")
    if not target.startswith(("http://", "https://")):
        return web.Response(status=400, text="bad url")
    log.info("   purl -> %s", target[:80])

    timeout = _ah.ClientTimeout(total=None, connect=10, sock_read=30)
    try:
        upstream = _ah.ClientSession(timeout=timeout)
        up_resp = await upstream.get(target, allow_redirects=True)
    except Exception as e:  # noqa: BLE001
        return web.Response(status=502, text=f"upstream: {e}")

    # пробрасываем аудио-тип и ICY-метаданные
    hdrs: dict[str, str] = {}
    for k, v in up_resp.headers.items():
        kl = k.lower()
        if kl in ("content-type", "content-length") or kl.startswith("icy-"):
            hdrs[k] = v
    hdrs.setdefault("Content-Type", "audio/mpeg")
    resp = web.StreamResponse(status=up_resp.status, headers=hdrs)
    await resp.prepare(request)
    try:
        async for chunk in up_resp.content.iter_chunked(8192):
            await resp.write(chunk)
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        up_resp.release()
        await upstream.close()
    return resp


async def stream(request):
    """GET /stream?stream_id=.. — отдать TTS/аудио (Ogg Opus).

    Если stream_id неизвестен — возвращаем ошибку как боевой mail.ru:
      {"qid":"<hex>","error":"4019:Stream expired or not exists",
       "code":4019,"reason":"Stream expired or not exists"}
    """
    sid = request.query.get("stream_id", "")
    item = request.app["streams"].get(sid)
    if item is None:
        log.info("   stream sid=%s -> 404 (expired/not exists)", sid[:12])
        qid = __import__("secrets").token_hex(16)
        # Отдаём 404 (а не 200+JSON) — иначе прошивка декодит как opus и падает
        # с 'unsupported mime-type application/json'.
        return web.json_response({
            "qid": qid,
            "error": "4019:Stream expired or not exists",
            "code": 4019,
            "reason": "Stream expired or not exists",
        }, status=404)
    audio, ctype = item
    log.info("   stream sid=%s -> %dB %s", sid[:12], len(audio), ctype)
    return web.Response(body=audio, content_type=ctype)


# ---- аккаунт ----------------------------------------------------------------
def _model_from_device_id(device_id: str) -> str:
    parts = device_id.split(":")
    return parts[3] if len(parts) >= 4 else ""


async def account_create_xtoken(request):
    require_device_id(request)
    dev = request["device"]
    acc = dev.account_id or await request.app["users"].account_for_device(dev.device_id)
    if not acc:
        acc = "acc-" + C.uuid.uuid4().hex[:12]
    await request.app["reg"].register_device(dev.device_id, acc, "", "", "", device_type="app")
    xtoken = C.uuid.uuid4().hex + C.uuid.uuid4().hex[:4]
    request.app["xtokens"][xtoken] = acc
    log.info("   create_xtoken device=%s -> xtoken=%s.. account=%s",
             dev.device_id[-10:], xtoken[:8], acc)
    return ok({"xtoken": xtoken, "account_id": acc}, request=request)


async def account_attach(request):
    """POST /account/attach_xtoken — регистрация колонки по xtoken_id + serial."""
    require_device_id(request)
    reg, dev = request.app["reg"], request["device"]
    q = dict(request.query)
    body = await request.read()
    xtid = q.get("xtoken_id")
    serial = q.get("serial") or q.get("sn") or dev.serial
    legacy = q.get("xtoken")
    if not legacy and body:
        try:
            legacy = json.loads(body).get("xtoken")
        except Exception:
            pass
    log.info("   attach device=%s xtoken_id=%s serial=%s", dev.device_id[-10:],
             (xtid or "-")[:10], serial or "-")
    acc = None
    if xtid:
        if not serial:
            raise _ApiError(E_BAD_PARAM, "Bad param: serial is required", 400)
        acc = request.app["xtokens"].get(xtid)
        if acc is None:
            raise _ApiError(E_XTOKEN_INVALID, "XToken is not valid", 400)
    bound = await request.app["users"].account_for_device(dev.device_id)
    if bound:
        acc = bound
    if acc:
        dev.account_id = acc
    model = _model_from_device_id(dev.device_id)
    name = ("VK Capsula Mini-" + serial[-4:]) if serial else dev.device_id
    await reg.register_device(dev.device_id, acc, serial or "", model, name)
    await reg.issue_session(dev, legacy or xtid)
    await events.log_event("attach", device_id=dev.device_id,
                           account_id=dev.account_id, adopted=False)
    resp = ok({
        "session_secret": dev.session_secret,
        "session_id": dev.session_id,
        "account_id": dev.account_id,
        "coordinates": dev.coordinates,
        "with_secret": True,
        "xtoken_id": (xtid or legacy or "local-xtoken")[:36],
        "status": "ok",
    }, request=request)
    resp.headers["X-Vc-Device-Secret"] = dev.session_secret
    return resp


async def account_devices(request):
    require_device_id(request)
    dev = request["device"]
    acc = dev.account_id or await request.app["users"].account_for_device(dev.device_id)
    ws = request.app["ws_devices"]
    out = []
    for d in await request.app["reg"].devices_for_account(acc):
        meta = d.get("meta") or {}
        if meta.get("device_type") == "app":
            continue
        out.append({
            "id": d["device_id"],
            "revision": 2,
            "serial": d.get("serial") or "",
            "model": d.get("model") or "",
            "firmware": None,
            "mac_address": None,
            "device_type": meta.get("device_type", "speaker"),
            "name": meta.get("name") or d["device_id"],
            "type": d.get("model") or "",
            "stereo": {"pair": None, "is_master": None, "supported": False},
            "is_owned": True,
            "is_online": d["device_id"] in ws,
        })
    return ok(out, request=request)


async def account_info(request):
    """GET /account/info — проверка валидности сессии.

    Прошивка (attach_xtoken.c) ходит сюда после attach, ждёт те же поля,
    что в ответе attach_xtoken + settings (collection account_vk_info).
    """
    require_device_id(request)
    dev = request["device"]
    resp = ok({
        "account_id": dev.account_id or "local",
        "name": "Local Account",
        "bound": dev.session_secret is not None,
        "session_secret": dev.session_secret or "",
        "session_id": dev.session_id or "",
        "coordinates": dev.coordinates,
        "with_secret": True,
        "xtoken_id": "local-xtoken",
        "settings": {
            "user_id": dev.account_id or "local-user",
            "access_token": dev.session_secret or "local-access-token",
        },
    }, request=request)
    if dev.session_secret:
        resp.headers["X-Vc-Device-Secret"] = dev.session_secret
    return resp


async def account_reminders(request):
    """GET /account/settings/v1/reminders — пустой список, но 200 (а не 404)."""
    return ok({"settings": []}, request=request)


async def account_vk_upgrade_token(request):
    dev = request["device"]
    tok = (dev.session_secret if dev and dev.session_secret else "local-" + C.uuid.uuid4().hex)
    ttl = int(os.environ.get("MARUSIA_SESSION_TTL", "3600") or 3600) or 3600
    return web.json_response({"access_token": tok, "expires_in": ttl, "token_type": "Bearer"})


# ---- устройство / телеметрия (принять и не мешать) --------------------------
async def device_settings(request):
    """GET/PUT /device/settings/v1/{tail} — настройки по категории.

    Прошивка парсит `settings` разными коллекторами (settings/collection/*.c).
    Что-то ждут object, что-то array. Пустой {} валит `assistant not started`.
    `/alarms` — наш storage: возвращаем список как у боевого mail.ru
      (ответ строго как `{"result": [...]}`, не `{settings: [...]}`).
    """
    tail = request.match_info.get("tail", "")
    parts = tail.rstrip("/").split("/")
    name = parts[-1] if parts else ""
    # device_id у /device/settings/v1/<device_id>/<name> лежит в ПУТИ, не в query
    device_id = ("/".join(parts[:-1]) if len(parts) > 1 else "") or request["device"].device_id
    if name == "alarms":
        # mail.ru на /alarms отдаёт плоский result-массив, не settings-обёртку
        return ok(await request.app["alarms"].list_for(device_id), request=request)

    default = _settings_for(name)
    store = request.app["settings"]
    # массивные коллекции (alarm_music/alarm_sound) не персистим — отдаём дефолт
    if isinstance(default, list):
        return ok({"settings": default}, request=request)

    if request.method in ("PUT", "POST"):
        value = _parse_settings_body(await request.read(), default)
        await request.app["reg"].ensure(device_id)   # FK: строка device должна быть
        rev = await store.put(device_id, name, value)
        log.info("   settings PUT %s/%s rev=%s", device_id[-10:], name, rev)
        return ok({"settings": value, "revision": rev}, request=request)

    settings = await store.get(device_id, name, default=default)
    return ok({"settings": settings}, request=request)


def _parse_settings_body(raw: bytes, default: dict) -> dict:
    """Достаёт dict настроек из тела PUT/POST: {settings:{...}} или просто {...}."""
    if not raw:
        return dict(default)
    try:
        body = json.loads(raw)
    except Exception:  # noqa: BLE001
        return dict(default)
    if isinstance(body, dict):
        inner = body.get("settings")
        if isinstance(inner, dict):
            return inner
        return body
    return dict(default)


_GENERAL_SETTINGS = {
    "keyword_sound": False,
    "master_volume": 50,
    "alarm_volume": 50,
    "fw_label": "stable",
    "skillserver": "",
    "reminder_sync": False,
    "upnp_discovery": False,
    "clock_enabled": True,
    "bt_handsfree": True,
    "clock_brightness": 50,
    "blackbox_depth": 0,
    "demomode_enabled_at": 0,
}


def _settings_for(name: str):
    """Какую форму `settings` ждёт прошивка для данной коллекции (из .c-файлов)."""
    if name in ("alarms", "alarm_music", "alarm_sound"):
        return []  # alarm_settings.c: array
    if name == "general":
        return _GENERAL_SETTINGS
    if name == "content_privacy":
        return {"vk_messages": False}
    if name == "night_mode":
        return {"enabled": False}
    if name == "safe_mode":
        return {"enabled": False}
    # по умолчанию — object (это проходит для большинства collection-парсеров)
    return {}


async def rt_logs(request):
    return ok({}, request=request)


async def device_firmware(request):
    """GET /device/firmware — OTA. Прошивка ждёт boot_zone, version."""
    q = dict(request.query)
    return ok({
        "hasUpdate": False,
        "boot_zone": int(q.get("boot_zone", "1") or 1),
        "version": q.get("device_ver", "3.5"),
    }, request=request)


async def device_timezone(request):
    """GET /device/timezone/info — таймзона.

    Из прошивки (timezone_helpers.c): парсит либо TZif-бинарник, либо POSIX TZ
    (`M%hu.%hu.%hu` → `MSK-3`). JSON здесь не подходит.
    """
    return web.Response(text="MSK-3", content_type="text/plain")


async def content_privacy(request):
    """* /content_privacy — content_privacy_settings коллекция."""
    return ok({"settings": {"vk_messages": False}}, request=request)


async def speedtest_dl(request):
    # прошивка просто ждёт body ненулевой длины
    return web.Response(body=b"\x00" * 65536, content_type="application/octet-stream")


async def speedtest_up(request):
    # прошивка льёт тело; просто принимаем
    await request.read()
    return ok({}, request=request)


async def speedtest_res(request):
    return ok({
        "latency_ms": 20,
        "upload_rate_kbps": 10000,
        "download_rate_kbps": 50000,
    }, request=request)


async def data_sink(request):
    """Пустая приёмка для /data/crash_reports/* и /data/smartbox_abort."""
    await request.read()
    return ok({}, request=request)


async def device_stat(request):
    return ok({}, request=request)


async def device_toggles(request):
    return ok({"available_toggles": {"is_grpc_available": False}}, request=request)


async def connect(request):
    """/connect — постоянный WebSocket, по которому сервер пушит настройки
    и служебные сообщения, пока колонка в простое.

    Прошивка Нео цикл подключений (`Upgrade: websocket`): если ответить
    обычным 200 — через пару секунд голосом «потеряна связь с сервером».
    Поэтому апгрейдимся по-настоящему, держим сокет и логируем входящее;
    команды в сокет сюда прибивать не нужно (их колонка забирает ещё и
    через /phrase/result и /phrase/commands).
    """
    if getattr(request.app["cfg"], "require_registration", False):
        d = request["device"]
        if not (d and d.account_id):
            raise _ApiError(E_UNKNOWN_DEVICE, "Unknown device", 400)
    upgrade = request.headers.get("Upgrade", "").lower()
    sec_key = request.headers.get("Sec-WebSocket-Key", "")
    dev = (request.query.get("device_id", "") or "-")[-12:]
    if upgrade != "websocket":
        log.info("   /connect HTTP method=%s (без Upgrade)", request.method)
        return ok({}, request=request)

    ws = web.WebSocketResponse(heartbeat=30.0, timeout=60.0)
    await ws.prepare(request)
    dev_full = request.query.get("device_id", "") or dev
    request.app["ws_devices"][dev_full] = ws
    WS_ACTIVE.inc()
    log.info("   /connect WS OPEN dev=%s sec-key=%r", dev, sec_key)

    # всё, что успело накопиться в очереди админ-команд — отдать сразу
    pending = await request.app["cmdq"].drain(dev_full)
    if pending:
        envelope = {"phrase_result": {"commands": pending, "controls": []}}
        try:
            await ws.send_str(json.dumps(envelope, ensure_ascii=False))
            log.info("   /connect WS flush pending=%d dev=%s", len(pending), dev)
        except Exception as e:  # noqa: BLE001
            log.warning("   /connect WS flush fail dev=%s: %s", dev, e)

    listener = asyncio.create_task(_cmdq_listener(request.app, dev_full, ws))
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                log.info("   /connect WS <-- dev=%s text=%r", dev, msg.data[:300])
            elif msg.type == web.WSMsgType.BINARY:
                log.info("   /connect WS <-- dev=%s bin=%dB", dev, len(msg.data))
            elif msg.type == web.WSMsgType.ERROR:
                log.warning("   /connect WS err dev=%s %s", dev, ws.exception())
    finally:
        listener.cancel()
        if request.app["ws_devices"].get(dev_full) is ws:
            request.app["ws_devices"].pop(dev_full, None)
        WS_ACTIVE.dec()
        log.info("   /connect WS CLOSE dev=%s", dev)
    return ws


# ---- admin-управление колонкой ---------------------------------------------
def _decode_body(raw: bytes) -> str:
    for enc in ("utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


async def admin_say(request):
    """POST /admin/say?device_id=..&text=.. — поставить в очередь озвучку.

    Текст можно передать query-параметром text (url-encoded) или телом запроса.
    """
    dev_id = require_device_id(request)
    text = request.query.get("text") or _decode_body(await request.read())
    if not text:
        raise _ApiError(4002, "text is required", 400)
    cmd = await _make_tts_command(request, text)
    await request.app["cmdq"].push(dev_id, cmd)
    return ok({"queued": "tts", "pending": await request.app["cmdq"].pending(dev_id)}, request=request)


async def admin_command(request):
    """POST /admin/command?device_id=.. body {type,...} — произвольная команда.

    Если у устройства открыт /connect WS — шлём команду туда сразу,
    чтобы не ждать очередного phrase/create.
    """
    dev_id = require_device_id(request)
    try:
        spec = json.loads(await request.read())
    except Exception:
        raise _ApiError(4002, "json body {type,...} required", 400)
    cmd = C.build(spec)
    # доставка: в cmdq (Redis) + publish → живой /connect пушит в WS (cmdq-listener).
    # Так работает и из отдельной админки, без in-process доступа к ws_devices.
    await request.app["cmdq"].push(dev_id, cmd)
    await events.log_event("command", device_id=dev_id, type=cmd.get("type"), via="admin")
    log.info("   admin push device=%s command=%s (queued+published)", dev_id[-10:], cmd.get("type"))
    return ok({"queued": cmd, "pending": await request.app["cmdq"].pending(dev_id)}, request=request)


async def admin_history(request):
    """GET /admin/history?device_id=.. — журнал команд (pending/delivered) + счётчик."""
    dev_id = require_device_id(request)
    cmdq = request.app["cmdq"]
    return ok({"pending": await cmdq.pending(dev_id), "history": await cmdq.history(dev_id)}, request=request)


async def admin_flowmode(request):
    """POST /admin/flowmode?device_id=..&bg_music=URL&count=N
    Пушим команды в WS `/connect` сразу (без wake-word), плюс в очередь
    как фолбэк — если WS мёртв, применятся на ближайшем phrase/commands.
    """
    dev_id = require_device_id(request)
    cmdq = request.app["cmdq"]
    bg = request.query.get("bg_music") or None
    count = int(request.query.get("count", "1") or 1)
    show_clock = request.query.get("clock", "1") != "0"
    cbd = json.dumps({"dialogId": f"flow-{int(time.time())}"})

    cmds: list[dict] = []
    if show_clock:
        cmds.append(C.clock(enabled=True, brightness=80))
    cmds.append(C.enter_flowmode(callback_data=cbd))
    if bg:
        cmds.append(C.flowmode_bg_music(bg, count=count))

    for c in cmds:
        await cmdq.push(dev_id, c)
    # доставка в WS — через cmdq-listener на /connect (как и admin_command)
    log.info("   flowmode queued+published dev=%s cmds=%d", dev_id[-10:], len(cmds))
    return ok({"queued_types": [c["type"] for c in cmds],
               "pending": await cmdq.pending(dev_id)}, request=request)


async def admin_clock(request):
    """POST /admin/clock?device_id=..&on=1&brightness=80 — часы на LED."""
    dev_id = require_device_id(request)
    on = request.query.get("on", "1") != "0"
    br = int(request.query.get("brightness", "80"))
    await request.app["cmdq"].push(dev_id, C.clock(enabled=on, brightness=br))
    return ok({"queued": "led_digits_animation", "on": on,
               "pending": await request.app["cmdq"].pending(dev_id)}, request=request)


async def admin_flow_log(request):
    """GET /admin/flow-log?device_id=.. — последние распознанные фразы flowmode."""
    log_entries = request.app.get("flow_log", [])
    return ok({"items": log_entries[-50:]}, request=request)


async def admin_live_wav(request):
    """GET /admin/live.wav?device_id=.. — бесконечный WAV-стрим, что сейчас слышит
    колонка. Открывается в ffplay/mpv/VLC. Content-type audio/wav."""
    import asyncio as _a
    dev_id = request.query.get("device_id", "")
    subs_map = request.app.setdefault("live_subs", {})
    subs = subs_map.setdefault(dev_id, [])
    q: _a.Queue = _a.Queue(maxsize=100)
    subs.append(q)
    log.info("   live.wav subscriber dev=%s (total=%d)", dev_id[-10:], len(subs))

    resp = web.StreamResponse(status=200, headers={
        "Content-Type": "audio/wav",
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",
    })
    await resp.prepare(request)
    # «бесконечный» WAV: длина 0xFFFFFFFF, PCM 16-bit mono 16kHz
    wav_hdr = (b"RIFF" + (0xFFFFFFFF).to_bytes(4, "little") + b"WAVE"
               + b"fmt " + (16).to_bytes(4, "little")
               + (1).to_bytes(2, "little") + (1).to_bytes(2, "little")
               + (16000).to_bytes(4, "little") + (32000).to_bytes(4, "little")
               + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
               + b"data" + (0xFFFFFFFF).to_bytes(4, "little"))
    await resp.write(wav_hdr)
    try:
        while True:
            try:
                pcm = await _a.wait_for(q.get(), timeout=30)
            except _a.TimeoutError:
                await resp.write(b"\x00" * 3200)  # keep-alive тишиной
                continue
            await resp.write(pcm)
    except (ConnectionResetError, _a.CancelledError):
        pass
    finally:
        try:
            subs.remove(q)
        except ValueError:
            pass
        log.info("   live.wav subscriber off dev=%s", dev_id[-10:])
    return resp


async def admin_live_page(request):
    """GET /admin/live — простая HTML страница с инструкцией и браузерным
    плеером (через MediaSource не играет сырой WAV, но подскажет команду ffplay)."""
    dev = request.query.get("device_id", "")
    host = request.host
    html = f"""<!doctype html><meta charset=utf-8>
<title>Marusia live listen</title>
<style>body{{font:14px system-ui;padding:24px;max-width:720px;margin:auto;color:#eee;background:#111}}
code{{background:#222;padding:2px 6px;border-radius:4px}}</style>
<h2>Live-прослушивание колонки</h2>
<p>Device: <code>{dev or '(укажи ?device_id=...)'}</code></p>
<p><b>В терминале (лучший вариант):</b></p>
<pre><code>ffplay -nodisp -autoexit=0 "http://{host}/admin/live.wav?device_id={dev}"</code></pre>
<p>или <code>mpv</code> / <code>vlc</code> с тем же URL.</p>
<p><b>В браузере:</b> формат «бесконечный WAV» не умеют &lt;audio&gt;-теги. Либо
скачай стрим как файл и слушай потом (<code>curl -o x.wav</code>), либо
подключись к <code>ws://{host}/connect</code> своим клиентом.</p>
<audio controls src="/admin/live.wav?device_id={dev}" style="width:100%"></audio>
"""
    return web.Response(text=html, content_type="text/html")


async def admin_recordings(request):
    """GET /admin/recordings — список записанных flowmode-сессий."""
    import os as _os
    rec_dir = "/data/recordings"
    files = []
    if _os.path.isdir(rec_dir):
        for name in sorted(_os.listdir(rec_dir)):
            p = _os.path.join(rec_dir, name)
            files.append({"name": name, "size": _os.path.getsize(p),
                          "mtime": int(_os.path.getmtime(p))})
    return ok({"recordings": files}, request=request)


async def admin_mirror(request):
    """GET  /admin/mirror — статус зеркала (global + per-device + upstream).
    POST /admin/mirror?on=1[&device_id=..] — включить/выключить зеркало боевого
    mail.ru в рантайме (глобально или для одной колонки). Флаги — в Redis."""
    from .storage import rds
    if not rds.enabled():
        raise _ApiError(5000, "redis disabled — mirror toggle unavailable", 500)
    r = rds.client()
    if request.method == "POST":
        on = request.query.get("on", "1") not in ("0", "false", "no", "off")
        dev = request.query.get("device_id", "")
        key = f"mirror:dev:{dev}" if dev else "mirror:global"
        if on:
            await r.set(key, "1")
        else:
            await r.delete(key)
        scope = dev or "global"
        await events.log_event("audit", device_id=dev or None, event="mirror_toggle",
                               scope=scope, on=on)
        log.info("   MIRROR %s -> %s", scope, "ON" if on else "OFF")
        return ok({"mirror": "on" if on else "off", "scope": scope,
                   "upstream": os.environ.get("MARUSIA_MIRROR_UPSTREAM", "")}, request=request)
    g = await r.get("mirror:global")
    keys = await r.keys("mirror:dev:*")
    devs = [k.split("mirror:dev:", 1)[1] for k in keys]
    return ok({"global": g == "1", "devices": devs,
               "upstream": os.environ.get("MARUSIA_MIRROR_UPSTREAM", ""),
               "capability": bool(os.environ.get("MARUSIA_MIRROR_UPSTREAM"))}, request=request)


async def admin_mirror_log(request):
    """GET /admin/mirror/log?n=50 — последние n записей MITM-журнала (jsonl)."""
    path = os.environ.get("MARUSIA_MIRROR_LOG", "/data/mirror/all.jsonl")
    n = int(request.query.get("n", "50") or 50)
    lines = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-n:]
    except FileNotFoundError:
        pass
    items = []
    for ln in lines:
        try:
            items.append(json.loads(ln))
        except (json.JSONDecodeError, TypeError):
            items.append({"raw": ln.strip()})
    return ok({"count": len(items), "path": path, "items": items}, request=request)


async def admin_alarms(request):
    dev_id = require_device_id(request)
    return ok({"alarms": await request.app["alarms"].list_for(dev_id)}, request=request)


async def admin_events(request):
    """GET /admin/events?kind=&device_id=&n= — просмотр event_log (для админки)."""
    from sqlalchemy import select
    from .storage import db
    from .storage.models import EventLog
    if not db.enabled():
        return ok({"events": []}, request=request)
    kind = request.query.get("kind")
    dev = request.query.get("device_id")
    n = min(int(request.query.get("n", "100") or 100), 1000)
    q = select(EventLog)
    if kind:
        q = q.where(EventLog.kind == kind)
    if dev:
        q = q.where(EventLog.device_id == dev)
    q = q.order_by(EventLog.id.desc()).limit(n)
    async with db.sessionmaker()() as s:
        rows = (await s.scalars(q)).all()
    items = [{"id": r.id, "kind": r.kind, "device_id": r.device_id,
              "phrase_id": r.phrase_id, "payload": r.payload,
              "created_at": r.created_at.isoformat() if r.created_at else None}
             for r in rows]
    return ok({"events": items}, request=request)


async def admin_devices(request):
    reg = request.app["reg"]
    out = []
    for d in await reg.all():
        out.append({"device_id": d.device_id, "serial": d.serial, "account_id": d.account_id,
                    "bound": d.session_secret is not None, "adopted": d.adopted,
                    "pending": await request.app["cmdq"].pending(d.device_id),
                    "last_seen": int(d.last_seen)})
    return ok({"devices": out}, request=request)


async def admin_skills(request):
    items = [skill_catalog.as_dict(s) for s in skill_catalog.all_skills()]
    cats: dict[str, int] = {}
    for s in skill_catalog.all_skills():
        cats[s.category] = cats.get(s.category, 0) + 1
    return ok({"skills": items, "count": len(items), "categories": cats}, request=request)


async def admin_skill_set(request):
    try:
        spec = json.loads(await request.read())
    except Exception:
        raise _ApiError(4002, "json body {id, enabled?, priority?, responses?, match?} required", 400)
    sid = spec.get("id") or request.query.get("id", "")
    if not sid:
        raise _ApiError(4002, "id required", 400)
    allowed = ("enabled", "priority", "responses", "match", "title", "category", "commands")
    fields = {k: v for k, v in spec.items() if k in allowed}
    sk = await skill_catalog.set_override(sid, fields)
    if sk is None:
        raise _ApiError(4040, "unknown skill", 404)
    await events.log_event("audit", event="skill_update", skill=sid, fields=list(fields))
    log.info("   skill %s override %s", sid, list(fields))
    return ok({"skill": skill_catalog.as_dict(sk)}, request=request)


async def admin_skill_responses(request):
    body = await _json_body(request)
    sid = body.get("id") or request.query.get("id", "")
    texts = body.get("texts") or []
    if not sid or not isinstance(texts, list) or not texts:
        raise _ApiError(4002, "id and non-empty texts[] required", 400)
    if skill_catalog.get(sid) is None:
        raise _ApiError(4040, "unknown skill", 404)
    added = await skill_catalog.add_responses(sid, [str(t) for t in texts])
    total = await skill_catalog.count_responses(sid)
    await events.log_event("audit", event="skill_responses", skill=sid, added=added)
    return ok({"skill": sid, "added": added, "pool": total}, request=request)


async def admin_skill_webhook(request):
    body = await _json_body(request)
    sid = (body.get("id") or "").strip()
    url = (body.get("url") or "").strip()
    if not sid or not url:
        raise _ApiError(4002, "id and url required", 400)
    if not url.startswith(("http://", "https://")):
        raise _ApiError(4002, "url must be http(s)", 400)
    match = body.get("match")
    if not match:
        phrases = body.get("phrases") or []
        if not phrases:
            raise _ApiError(4002, "match[] or phrases[] required", 400)
        match = [{"any": [skill_catalog.normalize(p) for p in phrases]}]
    cfg = {"url": url, "skill_id": body.get("skill_id") or str(C.uuid.uuid4())}
    if body.get("timezone"):
        cfg["timezone"] = body["timezone"]
    if body.get("city"):
        cfg["city"] = body["city"]
    definition = {
        "id": sid, "title": body.get("title", sid),
        "category": body.get("category", "other"), "kind": "webhook",
        "enabled": bool(body.get("enabled", True)),
        "priority": int(body.get("priority", 40)),
        "icon": body.get("icon", ""), "match": match,
        "examples": body.get("examples", []), "config": cfg,
    }
    sk = await skill_catalog.upsert(definition)
    if sk is None:
        raise _ApiError(5000, "catalog store unavailable", 500)
    await events.log_event("audit", event="skill_webhook", skill=sid, url=url)
    log.info("   webhook skill %s -> %s", sid, url)
    return ok({"skill": skill_catalog.as_dict(sk)}, request=request)


async def admin_skill_test(request):
    text = request.query.get("text", "") or _decode_body(await request.read())
    norm = skill_catalog.normalize(text)
    hits = [s.id for s in skill_catalog.active() if skill_catalog.matches(s, norm)]
    return ok({"text": text, "norm": norm, "matched": hits,
               "resolved": hits[0] if hits else None}, request=request)


async def _json_body(request) -> dict:
    try:
        data = json.loads(await request.read() or b"{}")
    except Exception:
        raise _ApiError(4002, "json body required", 400)
    if not isinstance(data, dict):
        raise _ApiError(4002, "json object required", 400)
    return data


async def admin_users(request):
    return ok({"users": await request.app["users"].list_users()}, request=request)


async def admin_user_create(request):
    body = await _json_body(request)
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not username or not password:
        raise _ApiError(4002, "username and password required", 400)
    try:
        u = await request.app["users"].create_user(
            username, password, body.get("display_name", ""), bool(body.get("is_admin", False)))
    except ValueError as e:
        raise _ApiError(4090, str(e), 409)
    await events.log_event("audit", event="user_create", user_id=u["user_id"], username=username)
    return ok({"user": u}, request=request)


async def admin_user_delete(request):
    body = await _json_body(request)
    uid = body.get("user_id") or ""
    if not uid:
        raise _ApiError(4002, "user_id required", 400)
    ok_ = await request.app["users"].delete_user(uid)
    if not ok_:
        raise _ApiError(4040, "unknown user", 404)
    await events.log_event("audit", event="user_delete", user_id=uid)
    return ok({"deleted": uid}, request=request)


async def admin_user_password(request):
    body = await _json_body(request)
    uid, pw = body.get("user_id") or "", body.get("password") or ""
    if not uid or not pw:
        raise _ApiError(4002, "user_id and password required", 400)
    if not await request.app["users"].set_password(uid, pw):
        raise _ApiError(4040, "unknown user", 404)
    await events.log_event("audit", event="user_password", user_id=uid)
    return ok({"user_id": uid}, request=request)


async def admin_user_disable(request):
    body = await _json_body(request)
    uid = body.get("user_id") or ""
    disabled = bool(body.get("disabled", True))
    if not uid:
        raise _ApiError(4002, "user_id required", 400)
    if not await request.app["users"].set_disabled(uid, disabled):
        raise _ApiError(4040, "unknown user", 404)
    await events.log_event("audit", event="user_disable", user_id=uid, disabled=disabled)
    return ok({"user_id": uid, "disabled": disabled}, request=request)


async def admin_bind(request):
    body = await _json_body(request)
    device_id, uid = body.get("device_id") or "", body.get("user_id") or ""
    if not device_id or not uid:
        raise _ApiError(4002, "device_id and user_id required", 400)
    if not await request.app["users"].bind_device(device_id, uid, source="admin"):
        raise _ApiError(4040, "unknown user", 404)
    await events.log_event("audit", device_id=device_id, event="bind", user_id=uid)
    return ok({"device_id": device_id, "user_id": uid}, request=request)


async def admin_unbind(request):
    body = await _json_body(request)
    device_id = body.get("device_id") or ""
    if not device_id:
        raise _ApiError(4002, "device_id required", 400)
    if not await request.app["users"].unbind_device(device_id):
        raise _ApiError(4040, "binding not found", 404)
    await events.log_event("audit", device_id=device_id, event="unbind")
    return ok({"device_id": device_id}, request=request)


async def admin_pair_code(request):
    body = await _json_body(request)
    uid = body.get("user_id") or ""
    ttl = int(body.get("ttl", 600) or 600)
    if not uid:
        raise _ApiError(4002, "user_id required", 400)
    if await request.app["users"].get_user(uid) is None:
        raise _ApiError(4040, "unknown user", 404)
    code = await request.app["users"].create_pair_code(uid, ttl)
    return ok({"code": code, "user_id": uid, "ttl": ttl}, request=request)


async def admin_pair_redeem(request):
    body = await _json_body(request)
    code, device_id = body.get("code") or "", body.get("device_id") or ""
    if not code or not device_id:
        raise _ApiError(4002, "code and device_id required", 400)
    uid = await request.app["users"].redeem_pair_code(code, device_id)
    if uid is None:
        raise _ApiError(4030, "invalid or expired code", 403)
    await events.log_event("audit", device_id=device_id, event="pair", user_id=uid)
    return ok({"device_id": device_id, "user_id": uid}, request=request)


# ---- утиль ------------------------------------------------------------------
def _new_phrase_id() -> str:
    # Боевой mail.ru формат (snif-ed): YYYYMMDD-HHMM-xxxx-xxxxxxxxxxxx
    return f"{time.strftime('%Y%m%d-%H%M')}-{C.uuid.uuid4().hex[:4]}-{C.uuid.uuid4().hex[:12]}"

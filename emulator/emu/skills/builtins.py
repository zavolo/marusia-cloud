from __future__ import annotations

import json
import logging
import random
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import aiohttp

from . import catalog
from .engine import Reply
from .. import alarms as A, commands as C, radio

log = logging.getLogger("emu.skills")

_UNITS = {"ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3,
          "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9}
_TEENS = {"десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13,
          "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16,
          "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19}
_TENS = {"двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
         "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100}

_CITY_OFF = {
    "москв": 3, "питер": 3, "петербург": 3, "волгоград": 3, "волгодонск": 3,
    "калининград": 2, "самар": 4, "ижевск": 4, "екатеринбург": 5, "челябинск": 5,
    "магнитогорск": 5, "орск": 5, "пермь": 5, "уфа": 5, "омск": 6, "новосибирск": 7,
    "красноярск": 7, "кемеров": 7, "иркутск": 8, "улан-удэ": 8, "якутск": 9, "чита": 9,
    "владивосток": 10, "хабаровск": 10, "магадан": 11, "камчат": 12, "петропавловск": 12,
}


def ru_number(text: str):
    m = re.search(r"\d+", text)
    if m:
        v = int(m.group())
        return v if 0 <= v <= 1000 else None
    val, found = 0, False
    for w in text.split():
        if w in _TENS:
            val += _TENS[w]; found = True
        elif w in _TEENS:
            val += _TEENS[w]; found = True
        elif w in _UNITS:
            val += _UNITS[w]; found = True
    return val if found else None


async def alarm_ds(ctx) -> Reply | None:
    t = ctx.norm
    store = ctx.app["alarms"]
    dev = ctx.device_id
    if any(w in t for w in ("поставь", "заведи", "установи")):
        sec = A.parse_time(t)
        if sec is None:
            return None
        await store.set_alarm(dev, sec)
        return Reply(say=f"Завела будильник на {A.format_time(sec)}",
                     commands=[{"type": "alarms", "alarms": await store.list_for(dev)}])
    if any(w in t for w in ("удали", "отмен", "выключ")):
        if "все" in t:
            n = await store.delete_all(dev)
            say = f"Удалила {n} будильников" if n else "Активных будильников нет"
            return Reply(say=say, commands=[{"type": "alarms", "alarms": await store.list_for(dev)}])
        sec = A.parse_time(t)
        if sec is not None:
            n = await store.delete_by_time(dev, sec)
            say = f"Удалила будильник на {A.format_time(sec)}" if n else "Не нашла такого будильника"
            return Reply(say=say, commands=[{"type": "alarms", "alarms": await store.list_for(dev)}])
        n = await store.delete_all(dev)
        say = f"Удалила {n} будильников" if n else "Активных будильников нет"
        return Reply(say=say, commands=[{"type": "alarms", "alarms": await store.list_for(dev)}])
    if any(w in t for w in ("какой", "список", "какие")):
        act = [a for a in await store.list_for(dev) if a.get("enabled")]
        if not act:
            return Reply(say="Активных будильников нет")
        times = ", ".join(A.format_time(a["alarm_time"]) for a in act)
        return Reply(say=f"У вас будильники на {times}")
    return None


async def radio_play(ctx) -> Reply | None:
    q = ctx.norm.replace("включи", "").replace("поставь", "").strip()
    st = radio.find(q)
    if not st:
        return None
    cmd = C.radio_media(url=st["url"], title=st.get("title", ""),
                        artist=st.get("artist", st["name"]),
                        cover_url=st.get("coverUrl", ""))
    return Reply(say=f"Включаю {st.get('artist') or st['name']}", commands=[cmd])


async def stop_music(ctx) -> Reply | None:
    return Reply(commands=[C.stop(), C.pause()])


async def volume_up(ctx) -> Reply | None:
    return await _volume_step(ctx, +10)


async def volume_down(ctx) -> Reply | None:
    return await _volume_step(ctx, -10)


async def _volume_step(ctx, delta: int) -> Reply | None:
    levels = ctx.app.setdefault("volume_levels", {})
    cur = int(levels.get(ctx.device_id, 50))
    new = max(0, min(100, cur + delta))
    levels[ctx.device_id] = new
    return Reply(commands=[C.volume(new)])


async def petrovich(ctx) -> Reply | None:
    return Reply(
        say="Петрович уехал на говновозе!",
        commands=[C.digits_text("14:88", duration_ms=300000, brightness=100),
                  C.sound(url="https://vc.go.mail.ru/stream?stream_id=petrovich-sfx")],
    )


async def petrovich_found(ctx) -> Reply | None:
    return Reply(say="О, Петрович вернулся.",
                 commands=[C.stop(), C.pause(), C.clock(enabled=True, brightness=80)])


async def flowmode(ctx) -> Reply | None:
    cbd = json.dumps({"dialogId": ctx.pid})
    return Reply(say="Слушаю без перерыва.", listen=False,
                 commands=[C.clock(enabled=True, brightness=80),
                           C.enter_flowmode(callback_data=cbd)])


async def pretty_on(ctx) -> Reply | None:
    return Reply(say="Открываю глазки", commands=[C.pretty_mode(turn_on=True)])


async def pretty_off(ctx) -> Reply | None:
    return Reply(say="Закрываю глазки", commands=[C.pretty_mode(turn_on=False)])


async def clock_on(ctx) -> Reply | None:
    return Reply(say="Показываю часы.", commands=[C.clock(enabled=True, brightness=80)])


async def clock_off(ctx) -> Reply | None:
    return Reply(say="Часы спрятала.", commands=[C.clock(enabled=False)])


async def light_demo(ctx) -> Reply | None:
    return Reply(say="Включаю подсветку.", commands=[C.light_demo(enter=True)])


async def time_now(ctx) -> Reply | None:
    t = ctx.norm
    for city, off in _CITY_OFF.items():
        if city in t and ("сколько" in t or "который" in t or " в " in f" {t} "):
            hhmm = (datetime.now(timezone.utc) + timedelta(hours=off)).strftime("%H:%M")
            return Reply(say=f"Там сейчас {hhmm}")
    return Reply(say="Сейчас " + time.strftime("%H:%M"))


async def games_list(ctx) -> Reply | None:
    return Reply(say="Давай сыграем в «Угадай число». Скажи «угадай число», и я загадаю число от 1 до 100.")


async def game_guess_start(ctx) -> Reply | None:
    n = random.randint(1, 100)
    return Reply(say="Загадала число от 1 до 100. Назови своё.",
                 dialog={"skill": "game_guess", "state": {"n": n, "tries": 0}})


async def game_guess_turn(ctx, state) -> Reply:
    t = ctx.norm
    if any(w in t for w in ("хватит", "стоп", "отмена", "сдаюсь", "не хочу", "надоело")):
        return Reply(say=f"Это было число {state.get('n')}. Было весело!", dialog="end")
    g = ru_number(t)
    if g is None:
        return Reply(say="Назови число от 1 до 100 или скажи «хватит».")
    state["tries"] = state.get("tries", 0) + 1
    if g == state.get("n"):
        return Reply(say=f"Верно, это {state['n']}! Угадал за {state['tries']} попыток. Сыграем ещё?",
                     dialog="end")
    hint = "больше" if g < state["n"] else "меньше"
    return Reply(say=f"Моё число {hint}.",
                 dialog={"skill": "game_guess", "state": state})


async def profile(ctx) -> Reply | None:
    name = ctx.norm.split("называй меня", 1)[-1].strip() if "называй меня" in ctx.norm else ""
    if not name:
        return None
    names = ctx.app.setdefault("profile_names", {})
    names[ctx.device_id] = name
    return Reply(say=f"Хорошо, буду называть вас {name}")


async def skill_list(ctx) -> Reply | None:
    titles = [s.title for s in catalog.active() if s.title][:12]
    return Reply(say="Я умею: " + ", ".join(titles) + " и многое другое.")


async def greeting(ctx) -> Reply | None:
    cfg = ctx.app["cfg"]
    toks = ctx.norm.split()
    first_is_name = bool(toks) and toks[0].startswith("марус")
    if len(toks) == 1 and first_is_name:
        return Reply(say="Да")
    if "привет" in ctx.norm or first_is_name:
        return Reply(say=cfg.reply_text)
    return None


def _hex64(seed: str) -> str:
    import hashlib
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _days_word(n: int) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return "дней"
    u = n % 10
    if u == 1:
        return "день"
    if 2 <= u <= 4:
        return "дня"
    return "дней"


async def coin_flip(ctx) -> Reply | None:
    return Reply(say=random.choice(["Орёл!", "Решка!"]))


async def days_to_newyear(ctx) -> Reply | None:
    now = datetime.now()
    nxt = datetime(now.year + 1, 1, 1)
    d = (nxt.date() - now.date()).days
    return Reply(say=f"До Нового года {d} {_days_word(d)}.")


def _calc(t: str):
    for kw, op in (("плюс", "+"), ("прибавить", "+"), ("сложить", "+"),
                   ("минус", "-"), ("вычесть", "-"), ("отними", "-"),
                   ("умножить", "*"), ("умножь", "*"), ("помножь", "*"),
                   ("разделить", "/"), ("раздели", "/"), ("поделить", "/"), ("делить", "/")):
        if kw in t:
            left, right = t.split(kw, 1)
            a, b = ru_number(left), ru_number(right)
            if a is not None and b is not None:
                return a, b, op
    return None


async def calc(ctx) -> Reply | None:
    parsed = _calc(ctx.norm)
    if parsed is None:
        return Reply(say="Скажите пример, например: сколько будет пять плюс три.")
    a, b, op = parsed
    if op == "/" and b == 0:
        return Reply(say="На ноль делить нельзя.")
    val = a + b if op == "+" else a - b if op == "-" else a * b if op == "*" else round(a / b, 2)
    if isinstance(val, float) and val.is_integer():
        val = int(val)
    return Reply(say=f"Будет {val}.")


async def _webhook_post(url: str, payload: dict):
    try:
        timeout = aiohttp.ClientTimeout(total=6, connect=3)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(url, json=payload) as r:
                if r.status != 200:
                    return None, True, {}
                data = await r.json(content_type=None)
    except Exception as e:
        log.warning("навык-вебхук %s не ответил: %s", url, e)
        return None, True, {}
    resp = (data or {}).get("response") or {}
    states = {}
    if "session_state" in (data or {}):
        states["session_state"] = data["session_state"]
    if "user_state_update" in (data or {}):
        states["user_state_update"] = data["user_state_update"]
    return (resp.get("tts") or resp.get("text")), bool(resp.get("end_session")), states


async def _webhook_call(ctx, st: dict, new: bool) -> Reply:
    state = st.get("state") or {"session": {}, "user": {}, "application": {}}
    payload = {
        "meta": {
            "client_id": "MailRu-VC/1.0",
            "locale": "ru_RU",
            "timezone": st.get("timezone", "Europe/Moscow"),
            "interfaces": {"screen": [], "audio_player": []},
            "_city_ru": st.get("city", ""),
        },
        "request": {
            "command": ctx.norm,
            "original_utterance": ctx.asr_text or ctx.norm,
            "type": "SimpleUtterance",
            "intents": ["start_dialog"] if new else [],
            "nlu": {"tokens": ctx.norm.split(), "entities": []},
        },
        "session": {
            "session_id": st["session_id"],
            "user_id": st["user_id"],
            "skill_id": st["skill_id"],
            "new": new,
            "message_id": st.get("message_id", 0),
            "user": {"user_id": st["user_sub"]},
            "application": {"application_id": st["user_id"], "application_type": "mobile"},
            "auth_token": st["auth_token"],
        },
        "state": state,
        "version": "1.0",
    }
    text, end, states = await _webhook_post(st["url"], payload)
    st["message_id"] = st.get("message_id", 0) + 1
    if states:
        ns = {"session": dict(state.get("session") or {}),
              "user": dict(state.get("user") or {}),
              "application": dict(state.get("application") or {})}
        if "session_state" in states:
            ns["session"] = states["session_state"]
        if "user_state_update" in states:
            ns["user"].update(states["user_state_update"] or {})
        st["state"] = ns
    if text is None:
        return Reply(say="Навык сейчас недоступен.", dialog="end")
    if end:
        return Reply(say=text, listen=False, dialog="end")
    return Reply(say=text, dialog={"skill": st["skill"], "state": st})


async def webhook_invoke(sk, ctx) -> Reply | None:
    cfg = sk.config or {}
    url = cfg.get("url")
    if not url:
        return None
    dev = ctx.device_id or "anon"
    st = {
        "skill": sk.id, "url": url,
        "skill_id": cfg.get("skill_id") or sk.id,
        "session_id": str(uuid.uuid4()),
        "user_id": _hex64("marusia-user:" + dev),
        "user_sub": _hex64("marusia-usersub:" + dev + ":" + sk.id),
        "auth_token": _hex64("marusia-auth:" + dev + ":" + sk.id),
        "message_id": 0,
        "timezone": cfg.get("timezone", "Europe/Moscow"),
        "city": cfg.get("city", ""),
        "state": {"session": {}, "user": {}, "application": {}},
    }
    return await _webhook_call(ctx, st, True)


async def webhook_turn(ctx, state) -> Reply:
    return await _webhook_call(ctx, state, False)


HANDLERS = {
    "alarm_ds": alarm_ds,
    "radio": radio_play,
    "stop_music": stop_music,
    "volume_up": volume_up,
    "volume_down": volume_down,
    "petrovich": petrovich,
    "petrovich_found": petrovich_found,
    "flowmode": flowmode,
    "pretty_mode": pretty_on,
    "pretty_mode_off": pretty_off,
    "clock": clock_on,
    "clock_off": clock_off,
    "light_demo": light_demo,
    "time": time_now,
    "profile": profile,
    "skill_list": skill_list,
    "greeting": greeting,
    "games": games_list,
    "game_guess": game_guess_start,
    "coin": coin_flip,
    "newyear": days_to_newyear,
    "calc": calc,
}

DIALOGS = {
    "game_guess": game_guess_turn,
}

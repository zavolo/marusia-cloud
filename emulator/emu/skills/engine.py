from __future__ import annotations

from dataclasses import dataclass, field

from . import catalog

NOISE = {"", "а", "о", "э", "м", "мм", "аа", "ээ", "хм"}


@dataclass
class Reply:
    skill: str = ""
    intent: str = ""
    say: str | None = None
    commands: list = field(default_factory=list)
    listen: bool | None = None
    dialog: object = None


@dataclass
class Context:
    app: object
    device_id: str
    asr_text: str
    norm: str
    activation_type: str = "kws"
    pid: str = ""


def _is_echo(norm: str, reply_text: str) -> bool:
    rl = reply_text.lower().strip(".!? ")
    if rl and rl in norm:
        return True
    for frag in ("это маруся", "привет это", "тест прошел"):
        if frag in norm:
            return True
    return False


def _apply_dialog(ctx: Context, reply: Reply) -> None:
    dialogs = ctx.app.setdefault("dialogs", {})
    if reply.dialog == "end":
        dialogs.pop(ctx.device_id, None)
    elif isinstance(reply.dialog, dict):
        dialogs[ctx.device_id] = reply.dialog


async def resolve(ctx: Context) -> Reply | None:
    norm = ctx.norm
    if not norm or norm in NOISE:
        return None
    cfg = ctx.app["cfg"]
    if _is_echo(norm, cfg.reply_text):
        return None
    from . import builtins as B
    active = (ctx.app.get("dialogs") or {}).get(ctx.device_id)
    if active and ctx.activation_type == "listen_command":
        sid = active.get("skill", "")
        fn = B.DIALOGS.get(sid)
        if fn is None:
            sk = catalog.get(sid)
            if sk is not None and sk.kind == "webhook":
                fn = B.webhook_turn
        if fn is not None:
            reply = await fn(ctx, active.get("state", {}))
            if reply is not None:
                reply.skill = reply.skill or active["skill"]
                reply.intent = reply.intent or f"{active['skill']}.turn"
                if reply.listen is None:
                    reply.listen = True
                _apply_dialog(ctx, reply)
                return reply
    for sk in catalog.active():
        if not catalog.matches(sk, norm):
            continue
        reply = await _dispatch(sk, ctx)
        if reply is None:
            continue
        reply.skill = reply.skill or sk.id
        reply.intent = reply.intent or f"{sk.id}.reply"
        if reply.listen is None:
            reply.listen = sk.id != "flowmode"
        _apply_dialog(ctx, reply)
        return reply
    return None


async def _dispatch(sk, ctx) -> Reply | None:
    if sk.kind == "dynamic":
        from . import builtins as B
        fn = B.HANDLERS.get(sk.id)
        if fn is None:
            return None
        return await fn(ctx)
    if sk.kind == "webhook":
        from . import builtins as B
        return await B.webhook_invoke(sk, ctx)
    if sk.kind == "say":
        txt = await catalog.random_response(sk.id)
        return Reply(say=txt) if txt else None
    if sk.kind in ("command", "media"):
        from .. import commands as C
        return Reply(commands=[C.build(dict(c)) for c in sk.commands])
    return None

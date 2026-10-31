from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import wave
from io import BytesIO

from aiohttp import web

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("asr")

_MODEL = None
_LOCK = threading.Lock()
_MODEL_PATH = os.environ.get("MARUSIA_VOSK_MODEL", "/app/vosk-model")
_SAMPLE_RATE = int(os.environ.get("MARUSIA_ASR_RATE", "16000"))


def _load():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    if not _MODEL_PATH or not os.path.isdir(_MODEL_PATH):
        log.warning("vosk model dir missing: %r", _MODEL_PATH)
        return None
    import vosk
    vosk.SetLogLevel(-1)
    _MODEL = vosk.Model(_MODEL_PATH)
    log.info("vosk model loaded: %s", _MODEL_PATH)
    return _MODEL


def _extract_pcm(buf: bytes) -> bytes:
    if not buf:
        return b""
    out = bytearray()
    i, n = 0, len(buf)
    while i < n:
        if buf[i:i + 4] == b"RIFF":
            try:
                w = wave.open(BytesIO(buf[i:i + 65536]), "rb")
                out.extend(w.readframes(w.getnframes()))
                i += 8 + int.from_bytes(buf[i + 4:i + 8], "little")
                continue
            except Exception:
                pass
        i += 1
    return bytes(out)


def _recognize(buf: bytes) -> str:
    model = _load()
    if model is None:
        return ""
    pcm = _extract_pcm(buf)
    if len(pcm) < 3200:
        return ""
    import vosk
    with _LOCK:
        rec = vosk.KaldiRecognizer(model, _SAMPLE_RATE)
        rec.SetWords(False)
        rec.AcceptWaveform(pcm)
        res = json.loads(rec.FinalResult() or "{}")
    return (res.get("text") or "").strip()


async def asr(request: web.Request) -> web.Response:
    buf = await request.read()
    t0 = time.perf_counter()
    try:
        text = await asyncio.get_running_loop().run_in_executor(None, _recognize, buf)
    except Exception as e:
        log.exception("recognize failed")
        return web.json_response({"error": str(e), "text": ""}, status=500)
    ms = int((time.perf_counter() - t0) * 1000)
    log.info("asr %dB -> %r (%dms)", len(buf), text[:60], ms)
    return web.json_response({"text": text, "ms": ms})


async def healthz(_req) -> web.Response:
    return web.json_response({"ok": _load() is not None, "model": _MODEL_PATH})


def main():
    _load()
    app = web.Application(client_max_size=32 * 1024 * 1024)
    app.router.add_post("/asr", asr)
    app.router.add_get("/healthz", healthz)
    web.run_app(app, host="0.0.0.0", port=8082, print=None)


if __name__ == "__main__":
    main()

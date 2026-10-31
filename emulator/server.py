#!/usr/bin/env python3
"""Marusia / VK Capsula Neo — эмулятор облака.

Говорит на протоколе колонки (реверс прошивки BES2300WP + живой /trace-захват),
чтобы колонка обращалась к нам вместо vc.go.mail.ru.

Чтобы направить РЕАЛЬНОЕ устройство сюда (проверено кодом и вживую):
  колонка держит TLS в режиме MBEDTLS_SSL_VERIFY_REQUIRED и сверяет hostname
  (mbedtls_ssl_set_hostname). Самоподписанный / свой-CA серт отвергается. Поэтому:
    1. свой домен + валидный Let's Encrypt-серт (верификация через DNS-TXT, корни
       ISRG X1/X2 есть в бандле колонки);
    2. UART RAM-патч строки хоста в ssl_ctx+0x498 -> твой домен;
    3. DNS: твой домен -> этот сервер.
  Автономная отладка — через mock_speaker.py (TLS не нужен).

Запуск:
  python server.py                                   # http :8080 (mock/отладка)
  python server.py --tls cert.pem key.pem --port 443 # https (для железа)
  python server.py --tls cert.pem key.pem --port 443 --tts-cmd "piper --model ru.onnx -f -"
"""
from __future__ import annotations

import argparse
import logging
import os
import ssl

from aiohttp import web

from emu import Config, make_app


def parse_args() -> Config:
    ap = argparse.ArgumentParser(description="Marusia cloud emulator")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--tls", nargs=2, metavar=("CERT", "KEY"), help="cert.pem key.pem для HTTPS")
    ap.add_argument("--strict-auth", action="store_true",
                    help="неизвестный Bearer на phrase -> 401 (колонка сделает re-attach)")
    ap.add_argument("--require-registration", action="store_true",
                    default=bool(os.environ.get("MARUSIA_REQUIRE_REGISTRATION")),
                    help="/connect для непривязанной колонки -> 4007 Unknown device (как боевое облако)")
    ap.add_argument("--coordinates", default=Config.coordinates,
                    help="geopos, отдаваемый колонке (lat,lon)")
    ap.add_argument("--tts-cmd", default=None,
                    help="внешняя TTS: команда, текст в stdin, WAV в stdout")
    ap.add_argument("--tts-bitrate", default=Config.tts_bitrate)
    ap.add_argument("--reply", default=Config.reply_text, help="фикс. ответ ассистента")
    ap.add_argument("--static-audio", default=os.environ.get("MARUSIA_STATIC_AUDIO"),
                    help="путь к Ogg Opus, который заменяет TTS в каждом ответе")
    a = ap.parse_args()
    return Config(
        host=a.host, port=a.port,
        tls_cert=a.tls[0] if a.tls else None,
        tls_key=a.tls[1] if a.tls else None,
        strict_auth=a.strict_auth, require_registration=a.require_registration,
        coordinates=a.coordinates,
        tts_cmd=a.tts_cmd, tts_bitrate=a.tts_bitrate, reply_text=a.reply,
        static_audio=a.static_audio,
    )


def main():
    from emu.obs import logging as obs_logging
    obs_logging.setup(level="INFO")
    cfg = parse_args()

    ssl_ctx = None
    if cfg.use_tls:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(cfg.tls_cert, cfg.tls_key)

    logging.getLogger("emu").info(
        "Marusia emulator on %s:%d (%s), tts=%s, strict_auth=%s",
        cfg.host, cfg.port, "https" if ssl_ctx else "http",
        cfg.tts_cmd or "tone->opus", cfg.strict_auth)
    web.run_app(make_app(cfg), host=cfg.host, port=cfg.port, ssl_context=ssl_ctx, print=None)


if __name__ == "__main__":
    main()

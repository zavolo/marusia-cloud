"""Vosk-STT: распознавание речи из WAV-чанков, которые колонка шлёт на /phrase/add.

Колонка шлёт PCM 16kHz mono S16LE, завёрнутый в WAV-заголовок каждый чанк.
Собираем все sample bytes в один буфер (пропуская RIFF-headers), скармливаем Vosk.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import wave
from io import BytesIO

log = logging.getLogger("asr")

_MODEL = None
_LOCK = threading.Lock()


def _load():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    path = os.environ.get("MARUSIA_VOSK_MODEL", "")
    if not path or not os.path.isdir(path):
        log.info("vosk disabled (no model at %r)", path)
        return None
    try:
        import vosk  # noqa: F401
        vosk.SetLogLevel(-1)
        _MODEL = vosk.Model(path)
        log.info("vosk model loaded from %s", path)
    except Exception as e:  # noqa: BLE001
        log.warning("vosk load failed: %s", e)
        _MODEL = None
    return _MODEL


def available() -> bool:
    return _load() is not None


def _extract_pcm(buf: bytes) -> bytes:
    """Из потока склеенных WAV-чанков собрать чистый PCM 16kHz mono.

    Прошивка шлёт на каждом /phrase/add маленький WAV-файл: RIFF-header +
    data-chunk ~5100 B. Берём только body WAV, перекладываем в PCM.
    """
    if not buf:
        return b""
    out = bytearray()
    i = 0
    n = len(buf)
    while i < n:
        if buf[i:i + 4] == b"RIFF":
            try:
                w = wave.open(BytesIO(buf[i:i + 65536]), "rb")
                out.extend(w.readframes(w.getnframes()))
                size = 8 + int.from_bytes(buf[i + 4:i + 8], "little")
                i += size
                continue
            except Exception:  # noqa: BLE001
                pass
        i += 1
    return bytes(out)


def recognize(wav_chunks: list[bytes]) -> str:
    """Склеивает все WAV-чанки, прогоняет через Vosk, возвращает text."""
    model = _load()
    if model is None:
        return ""
    pcm = _extract_pcm(b"".join(wav_chunks))
    if len(pcm) < 3200:  # <100ms аудио = шум
        return ""
    try:
        import vosk
        with _LOCK:
            rec = vosk.KaldiRecognizer(model, 16000)
            rec.SetWords(False)
            rec.AcceptWaveform(pcm)
            res = json.loads(rec.FinalResult() or "{}")
        return (res.get("text") or "").strip()
    except Exception as e:  # noqa: BLE001
        log.warning("vosk recognize failed: %s", e)
        return ""

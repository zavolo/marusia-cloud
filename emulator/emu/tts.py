"""TTS-движок: текст -> Ogg Opus (формат, который колонка ждёт в команде tts).

Колонка тянет аудио с /stream и декодирует по полю encoder='opus'. Если отдать
не тот формат (WAV/сырой поток) — декодер падает и колонка ребутится (наблюдалось
на форуме при проигрывании музыки с локального сервера). Поэтому гоним ВСЁ через
ffmpeg -> Ogg Opus.

Источник PCM:
  - внешняя TTS-команда (cfg.tts_cmd), читает текст из stdin, отдаёт WAV в stdout; либо
  - фолбэк-тон (чистый Opus, чтобы колонка что-то проиграла и не упала).
"""
from __future__ import annotations

import asyncio
import io
import logging
import math
import os
import struct
import wave

import aiohttp

log = logging.getLogger("emu")


def _tone_wav(seconds: float = 0.6, freq: float = 440.0, sr: int = 48000) -> bytes:
    frames = bytearray()
    for i in range(int(sr * seconds)):
        frames += struct.pack("<h", int(32767 * 0.25 * math.sin(2 * math.pi * freq * i / sr)))
    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes(frames)
    w.close()
    return buf.getvalue()


class TTSEngine:
    """WAV -> Ogg Opus через ffmpeg, с кэшом по (text, encoder)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._cache: dict[tuple[str, str], tuple[bytes, str]] = {}

    async def warmup(self, phrases: list[str]) -> None:
        """Прогревает TTS-кэш типовыми фразами (RVC-синтез идёт 10–20 сек на фразу,
        колонка так долго ждать не будет — лучше заранее)."""
        for text in phrases:
            try:
                await self.synth(text, self.cfg.tts_encoder)
                log.info("tts warmup cached: %r", text)
            except Exception as e:  # noqa: BLE001
                log.warning("tts warmup fail %r: %s", text, e)

    async def synth(self, text: str, encoder: str | None = None) -> tuple[bytes, str]:
        encoder = encoder or self.cfg.tts_encoder
        key = (text, encoder)
        if key in self._cache:                       # L1: in-memory
            return self._cache[key]

        # L2: персистентный кэш (диск + Postgres) — переживает рестарт
        from .storage import tts_cache
        voice = os.environ.get("MARUSIA_RVC_VOICE", "")
        ck = tts_cache.key_for(text, encoder, voice)
        hit = await tts_cache.get(ck)
        if hit is not None:
            self._cache[key] = hit
            return hit

        # RVC-сервис (Silero → RVC голос персонажа → Opus) — если задан URL.
        rvc_url = os.environ.get("MARUSIA_RVC_URL")
        if rvc_url and encoder == "opus":
            try:
                opus = await self._rvc(rvc_url, text)
                self._cache[key] = (opus, "audio/ogg")
                await tts_cache.put(ck, text, encoder, voice, opus, "audio/ogg")
                return self._cache[key]
            except Exception as e:  # noqa: BLE001
                log.warning("rvc tts failed (%s), fallback to local", e)

        wav = await self._source_wav(text)
        if encoder == "opus":
            data = await self._to_opus(wav)
            ctype = "audio/ogg"
        else:
            data, ctype = wav, "audio/wav"
        self._cache[key] = (data, ctype)
        await tts_cache.put(ck, text, encoder, voice, data, ctype)
        return data, ctype

    async def _rvc(self, url: str, text: str) -> bytes:
        voice = os.environ.get("MARUSIA_RVC_VOICE", "valakas")
        timeout = aiohttp.ClientTimeout(total=90)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(url, json={"text": text, "voice": voice}) as r:
                r.raise_for_status()
                return await r.read()

    async def _source_wav(self, text: str) -> bytes:
        if self.cfg.tts_cmd:
            try:
                return await self._external_tts(text)
            except Exception as e:  # noqa: BLE001
                log.warning("external TTS failed (%s), fallback to tone", e)
        return _tone_wav()

    async def _external_tts(self, text: str) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            *self.cfg.tts_cmd.split(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate(text.encode("utf-8"))
        if proc.returncode != 0 or not out:
            raise RuntimeError((err or b"").decode("utf-8", "replace")[:200] or "no output")
        return out

    async def _to_opus(self, wav: bytes) -> bytes:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "wav", "-i", "pipe:0",
            "-ar", str(self.cfg.tts_samplerate), "-ac", "1",
            "-c:a", "libopus", "-b:a", self.cfg.tts_bitrate,
            "-f", "opus", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate(wav)
        if proc.returncode != 0 or not out:
            raise RuntimeError((err or b"").decode("utf-8", "replace")[:200] or "ffmpeg failed")
        return out

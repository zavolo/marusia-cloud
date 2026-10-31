"""Конфигурация эмулятора."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Config:
    host: str = "0.0.0.0"
    port: int = 8080
    tls_cert: str | None = None
    tls_key: str | None = None

    # auth: strict -> неизвестный Bearer на phrase даёт 401 (колонка делает re-attach)
    strict_auth: bool = False
    require_registration: bool = False
    # geopos, который отдаём колонке в result.coordinates (она его сохраняет)
    coordinates: str = "55.751244,37.618423"

    # TTS
    tts_encoder: str = "opus"       # формат, который колонка ждёт в команде tts
    tts_bitrate: str = "32k"
    tts_samplerate: int = 48000
    # внешняя TTS: команда-шаблон, {text} подставляется, WAV в stdout.
    # напр.: 'piper --model ru.onnx --output_file -'  (текст в stdin)
    tts_cmd: str | None = None

    # заглушка ответа ассистента (пока без реального STT/LLM)
    reply_text: str = "Привет! Я твой локальный сервер, замена облака Маруси."

    # если задан — на любой голосовой запрос вместо TTS отдаётся этот
    # готовый Ogg Opus (путь в контейнере), а колонка играет файл.
    static_audio: str | None = None

    @property
    def use_tls(self) -> bool:
        return bool(self.tls_cert and self.tls_key)

"""Команды облако -> колонка.

Ответ сервера несёт массив commands; у каждой есть type, по которому колонка
запускает обработчик (в логах 'run <type>'). Набор type и поля восстановлены из
логов прошивки (command_runner). Для неизвестных точных вложенностей поля названы
по логам ('url', 'count', 'turn_on', 'timestamp', 'delta', 'cancel', 'length',
'callback_data', 'text', 'event', 'value'), что достаточно для управления.

CommandQueue — отложенные команды на устройство, отдаются в /phrase/result и
/phrase/commands (двойник облачного пуша).
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque


# ---- билдеры команд (возвращают dict под массив commands) --------------------
def tts(text: str, stream_id: str, encoder: str = "opus", blocking: bool = True,
        voice: str = "marusya", codec: str = "opus") -> dict:
    """Озвучить текст. Полный набор полей как у боевого mail.ru (из эталона
    v3rt3p/marusya-server/src/vc-api.ts): без них прошивка игнорит команду.
    """
    return {
        "type": "tts",
        "text": text,
        "stream_id": stream_id,
        "blocking": blocking,
        "encoder": encoder,
        "encoder_bitrate": None,
        "encoder_frame_size": None,
        "force_say": False,
        "kws_skip": None,
        "long_reader_request": False,
        "model_name": "tts",
        "normalize": True,
        "speed": 1,
        "stream_hls": False,
    }


def text(value: str) -> dict:
    """Распознанный/отображаемый текст (на Neo без экрана 'unsupported', но шлётся)."""
    return {"type": "text", "text": value}


def listen(dialog_id: str | None = None, min_waiting_time: int = 4,
           mute_activation_sound: bool = False) -> dict:
    """Продолжить слушать. `min_waiting_time=4` сек паузы — важно для anti-feedback
    (колонка не слушает собственный tts после его окончания)."""
    import json as _json
    cmd = {
        "type": "listen",
        "blocking": True,
        "min_waiting_time": min_waiting_time,
        "mute_activation_sound": mute_activation_sound,
    }
    if dialog_id is not None:
        cmd["callback_data"] = _json.dumps({"dialogId": dialog_id})
    return cmd


def sound(url: str) -> dict:
    """Проиграть звук по URL (run 'sound', url=...)."""
    return {"type": "sound", "url": url}


def play_url(url: str) -> dict:
    return {"type": "play", "url": url}


def playlist(tracks: list[dict], eof: bool = False,
             seek_track: int = 0, seek_second: int = 0) -> dict:
    """Плейлист (run 'playlist', eof=, seek_track=, seek_second=)."""
    return {"type": "playlist", "tracks": tracks, "eof": eof,
            "seek_track": seek_track, "seek_second": seek_second}


def volume(value: int) -> dict:
    """Прошивка (command_runner.c) ждёт тип 'volume_control' и поле level."""
    return {"type": "volume_control", "level": int(value)}


def pause(delay: int = 0) -> dict:
    return {"type": "pause_control", "delay": int(delay)}


def resume() -> dict:
    return {"type": "resume_control"}


def stop() -> dict:
    return {"type": "stop_control"}


def next_track() -> dict:
    return {"type": "next_control"}


def prev_track() -> dict:
    return {"type": "previous_control"}


def rewind(position: int, elapsed: int = 0) -> dict:
    return {"type": "rewind_control", "position": position, "elapsed": elapsed}


def repeat(count: int = 1) -> dict:
    return {"type": "repeat_control", "count": int(count)}


def repeat_playlist(count: int = 1) -> dict:
    return {"type": "repeat_playlist_control", "count": int(count)}


def shuffle() -> dict:
    return {"type": "shuffle_control"}


def playlist(tracks: list[dict], seek_track: int = 0, seek_second: int = 0,
             media_type: int = 1, tracks_type: str = "media") -> dict:
    """`playlist`-команда боевого mail.ru. media_type=1 — обычная музыка.
    Каждый трек: {url, meta:{artist, title, duration, id?, owner_id?, access_key?,
                             is_explicit:false, is_focus_track:false, track_code?, ads?}}.
    """
    return {
        "type": "playlist",
        "tracks_type": tracks_type,
        "media_type": media_type,
        "seek_track": seek_track,
        "seek_second": seek_second,
        "tracks": tracks,
    }


def radio_media(url: str, title: str = "", artist: str = "",
                cover_url: str = "", stat_flags: str = "{}",
                autoplay: bool = True, append: bool = False) -> dict:
    """`media`-команда для радиостанции (media_type=2). Структура точно от mail.ru.
    URL `.mp3`/`.aacp`/`.m3u8` — прошивка проглатывает всё."""
    source = {
        "artist": artist, "media_type": "radio", "type": "radio",
        "uid": url, "phrase_id": "", "skill_name": "radio",
        "stat_flags": stat_flags,
    }
    return {
        "type": "media",
        "media_type": 2,
        "url": url,
        "title": title,
        "coverUrl": cover_url,
        "autoplay": autoplay,
        "append": append,
        "event_media_end": None,
        "kws_skip": None,
        "page_token": None,
        "action_text": "",
        "content_value": {},
        "need_vk_session": None,
        "stat_flags": stat_flags,
        "meta": {"artist": artist, "source": source},
    }


def media(tracks: list[dict], **kwargs) -> dict:
    """Старый универсальный media (для обратной совместимости)."""
    return {"type": "media", "tracks": tracks, **kwargs}


def mp3_track(url: str, artist: str = "", title: str = "", duration: int = 0,
              vk_id: int = 0, owner_id: int = 0, access_key: str = "") -> dict:
    """Один трек для playlist[]-команды (как у боевого mail.ru)."""
    meta = {"artist": artist, "title": title, "duration": int(duration),
            "is_explicit": False, "is_focus_track": False}
    if vk_id:
        meta["id"] = int(vk_id)
    if owner_id:
        meta["owner_id"] = int(owner_id)
    if access_key:
        meta["access_key"] = access_key
    return {"url": url, "meta": meta}


def demo_speech(text: str) -> dict:
    return {"type": "demo_speech", "text": text}


def enter_flowmode(callback_data: str = "") -> dict:
    """Непрерывное слушание без wake-word (run 'enter_flowmode')."""
    return {"type": "enter_flowmode", "callback_data": callback_data}


def flowmode_sound(url: str) -> dict:
    return {"type": "flowmode_sound", "url": url}


def flowmode_bg_music(url: str, count: int = 1) -> dict:
    return {"type": "flowmode_bg_music", "url": url, "count": count}


def bluetooth_pair(turn_on: bool = True) -> dict:
    return {"type": "bluetooth_pair", "turn_on": turn_on}


def pretty_mode(turn_on: bool = True) -> dict:
    return {"type": "pretty_mode", "turn_on": turn_on}


def led_digits_animation(payload: dict | None = None) -> dict:
    return {"type": "led_digits_animation", **(payload or {})}


def clock(enabled: bool = True, brightness: int = 80) -> dict:
    """Показать/спрятать часы на LED-дисплее Нео (поля из прошивки
    clock_led_animator.c: clock_control, clock_enabled, clock_brightness)."""
    return {
        "type": "led_digits_animation",
        "clock_control": True,
        "clock_enabled": bool(enabled),
        "clock_brightness": int(brightness),
    }


def digits_text(text: str, duration_ms: int = 60000, brightness: int = 100,
                iteration: int = 1) -> dict:
    """Показать произвольные цифры/строку на LED (`14:88`, `88:88` и т.п.).

    Поля payload: frames[], iteration_count, scheduled, start_at, stop_at.
    В frame пробуем ключи text/digits/value — чей-то должен подхватиться парсером.
    """
    frame = {"text": text, "digits": text, "value": text,
             "duration": int(duration_ms), "duration_ms": int(duration_ms),
             "brightness": int(brightness)}
    return {
        "type": "led_digits_animation",
        "clock_control": False,
        "clock_enabled": False,
        "frames": [frame],
        "iteration_count": int(iteration),
    }


def light_demo(enter: bool = True) -> dict:
    return {"type": "enter_light_demo" if enter else "exit_light_demo"}


def alarm(payload: dict) -> dict:
    return {"type": "alarm", **payload}


def reminder(payload: dict) -> dict:
    return {"type": "reminder", **payload}


def reminder_cancel() -> dict:
    return {"type": "reminder_cancel"}


def timer(timestamp: int, delta: int = 0, cancel: bool = False) -> dict:
    return {"type": "timer", "timestamp": timestamp, "delta": delta, "cancel": cancel}


def delay(length_ms: int) -> dict:
    return {"type": "delay", "length": length_ms}


def cancel_notification() -> dict:
    return {"type": "cancel_notification"}


def scenario_step(text_: str = "", event: str = "", callback_data: str = "") -> dict:
    return {"type": "scenario_step", "text": text_, "event": event,
            "callback_data": callback_data}


def send_event_data(event: str, callback_data: str = "") -> dict:
    return {"type": "send_event_data", "event": event, "callback_data": callback_data}


def speed_test() -> dict:
    return {"type": "speed_test"}


# билдеры по type-строке (для admin-API «отправь произвольную команду»).
# Короткие алиасы (volume/pause/next/...) и прошивкины имена (volume_control/
# pause_control/...) принимают оба — так удобнее писать в curl.
BUILDERS = {
    "tts": lambda p: tts(p["text"], p["stream_id"], p.get("encoder", "opus"),
                         p.get("blocking", True), p.get("voice", "marusya"),
                         p.get("codec", "opus")),
    "text": lambda p: text(p["text"]),
    "listen": lambda p: listen(),
    "sound": lambda p: sound(p["url"]),
    "play": lambda p: play_url(p["url"]),
    "playlist": lambda p: playlist(p["tracks"], p.get("eof", False),
                                   p.get("seek_track", 0), p.get("seek_second", 0)),
    "media": lambda p: media(p["tracks"], **{k: v for k, v in p.items()
                                             if k not in ("type", "tracks")}),
    "demo_speech": lambda p: demo_speech(p["text"]),
    "volume": lambda p: volume(p.get("value", p.get("level", 50))),
    "volume_control": lambda p: volume(p.get("level", p.get("value", 50))),
    "pause": lambda p: pause(p.get("delay", 0)),
    "pause_control": lambda p: pause(p.get("delay", 0)),
    "resume": lambda p: resume(),
    "resume_control": lambda p: resume(),
    "stop": lambda p: stop(),
    "stop_control": lambda p: stop(),
    "next": lambda p: next_track(),
    "next_control": lambda p: next_track(),
    "previous": lambda p: prev_track(),
    "previous_control": lambda p: prev_track(),
    "rewind": lambda p: rewind(p["position"], p.get("elapsed", 0)),
    "rewind_control": lambda p: rewind(p["position"], p.get("elapsed", 0)),
    "repeat": lambda p: repeat(p.get("count", 1)),
    "repeat_control": lambda p: repeat(p.get("count", 1)),
    "repeat_playlist": lambda p: repeat_playlist(p.get("count", 1)),
    "repeat_playlist_control": lambda p: repeat_playlist(p.get("count", 1)),
    "shuffle": lambda p: shuffle(),
    "shuffle_control": lambda p: shuffle(),
    "enter_flowmode": lambda p: enter_flowmode(p.get("callback_data", "")),
    "flowmode_sound": lambda p: flowmode_sound(p["url"]),
    "flowmode_bg_music": lambda p: flowmode_bg_music(p["url"], p.get("count", 1)),
    "bluetooth_pair": lambda p: bluetooth_pair(p.get("turn_on", True)),
    "pretty_mode": lambda p: pretty_mode(p.get("turn_on", True)),
    "led_digits_animation": lambda p: led_digits_animation(p.get("payload")),
    "light_demo": lambda p: light_demo(p.get("enter", True)),
    "enter_light_demo": lambda p: light_demo(True),
    "exit_light_demo": lambda p: light_demo(False),
    "alarm": lambda p: alarm({k: v for k, v in p.items() if k != "type"}),
    "reminder": lambda p: reminder({k: v for k, v in p.items() if k != "type"}),
    "reminder_cancel": lambda p: reminder_cancel(),
    "timer": lambda p: timer(p["timestamp"], p.get("delta", 0), p.get("cancel", False)),
    "delay": lambda p: delay(p.get("length_ms", p.get("length", 0))),
    "cancel_notification": lambda p: cancel_notification(),
    "speed_test": lambda p: speed_test(),
    "scenario_step": lambda p: scenario_step(p.get("text", ""), p.get("event", ""),
                                             p.get("callback_data", "")),
    "send_event_data": lambda p: send_event_data(p["event"], p.get("callback_data", "")),
    "send_event": lambda p: send_event_data(p["event"], p.get("callback_data", "")),
}


def build(spec: dict) -> dict:
    """spec = {"type": <t>, ...поля}. Известный type -> билдер; иначе — как есть."""
    t = spec.get("type", "")
    fn = BUILDERS.get(t)
    return fn(spec) if fn else dict(spec)


class CommandQueue:
    """Очередь команд + журнал доставки по device_id.

    Каждая команда проходит путь pending -> delivered: pending, пока колонка не
    заберёт её (в /phrase/result или опросом /phrase/commands), затем delivered.
    Журнал (история) хранит последние команды со статусом и временем.
    """

    def __init__(self, max_history: int = 100):
        self._pending: dict[str, deque] = defaultdict(deque)
        self._history: dict[str, deque] = defaultdict(deque)  # новые первыми
        self._seq = 0
        self.max_history = max_history

    def push(self, device_id: str, command: dict) -> dict:
        self._seq += 1
        entry = {
            "id": self._seq,
            "type": command.get("type", "?"),
            "command": command,
            "created_at": time.time(),
            "status": "pending",
            "delivered_at": None,
        }
        self._pending[device_id].append(entry)
        hist = self._history[device_id]
        hist.appendleft(entry)  # та же ссылка -> статус обновится и в истории
        while len(hist) > self.max_history:
            hist.pop()
        return entry

    def drain(self, device_id: str) -> list[dict]:
        q = self._pending.get(device_id)
        if not q:
            return []
        now = time.time()
        out = []
        while q:
            entry = q.popleft()
            entry["status"] = "delivered"
            entry["delivered_at"] = now
            out.append(entry["command"])
        return out

    def pending(self, device_id: str) -> int:
        return len(self._pending.get(device_id, ()))

    def history(self, device_id: str, limit: int = 50) -> list[dict]:
        items = list(self._history.get(device_id, ()))[:limit]
        return [
            {
                "id": e["id"],
                "type": e["type"],
                "command": e["command"],
                "created_at": e["created_at"],
                "status": e["status"],
                "delivered_at": e["delivered_at"],
            }
            for e in items
        ]


def new_stream_id() -> str:
    return str(uuid.uuid4())

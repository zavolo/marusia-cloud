"""Аутентификация устройства.

Реверс (Ghidra): FUN_2d1ac8c4 парсит из ответа result.{session_secret,session_id,
account_id,coordinates}; FUN_2d1bd09c строит Authorization: Bearer <session_secret>;
FUN_2d1bed24 добавляет device_id/device_ver/serial/session_id + X-Vc-Device-Secret.

Флоу: колонка -> POST /account/attach_xtoken (xtoken из приложения VK) -> облако выдаёт
сессию -> колонка носит Bearer <session_secret> на всех запросах.
"""
from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class Device:
    device_id: str
    serial: str = ""
    device_ver: str = ""
    device_secret: str = ""          # X-Vc-Device-Secret (хранимый секрет железа)
    session_secret: str | None = None  # он же Bearer
    session_id: str | None = None
    account_id: str | None = None
    coordinates: str = ""
    model: str = ""
    meta: dict = field(default_factory=dict)
    xtoken: str | None = None
    adopted: bool = False            # приняли легаси-Bearer от прошлого облака
    last_bearer: str = ""
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


class Registry:
    """Реестр устройств и сессий."""

    def __init__(self, default_coords: str, strict: bool):
        self._devs: dict[str, Device] = {}
        self.default_coords = default_coords
        self.strict = strict

    def all(self) -> list[Device]:
        return list(self._devs.values())

    def get(self, device_id: str) -> Device | None:
        return self._devs.get(device_id)

    def ensure(self, device_id: str) -> None:
        """Гарантирует наличие записи устройства (для путей, где device_id в URL)."""
        if device_id and device_id not in self._devs:
            self._devs[device_id] = Device(device_id=device_id,
                                           coordinates=self.default_coords)

    def touch(self, request) -> tuple[Device, str]:
        """Достаёт/создаёт устройство по заголовкам/параметрам. Тело не читает."""
        dev_id = request.query.get("device_id", "")
        serial = request.query.get("sn", "") or request.query.get("serial", "")
        dver = request.query.get("device_ver", "")
        sid = request.query.get("session_id", "")
        dsecret = request.headers.get("X-Vc-Device-Secret", "")
        bearer = request.headers.get("Authorization", "")
        if bearer.startswith("Bearer "):
            bearer = bearer[7:]

        dev = self._devs.get(dev_id)
        if dev is None:
            dev = self._devs[dev_id] = Device(device_id=dev_id, coordinates=self.default_coords)
        if serial:
            dev.serial = serial
        if dver:
            dev.device_ver = dver
        if dsecret:
            dev.device_secret = dsecret
        dev.last_bearer = bearer
        dev.last_seen = time.time()

        if sid and not self.strict:
            dev.session_id = sid
            if not dev.session_secret:
                dev.session_secret = bearer or secrets.token_urlsafe(32)
                dev.adopted = True
        if bearer and not dev.session_secret and not self.strict:
            dev.session_secret = bearer
            dev.adopted = True
        return dev, bearer

    def issue_session(self, dev: Device, xtoken: str | None = None) -> Device:
        """Выдать сессию (как облако на attach_xtoken)."""
        dev.session_secret = secrets.token_urlsafe(32)
        dev.session_id = uuid.uuid4().hex
        dev.account_id = dev.account_id or "acc-" + secrets.token_hex(4)
        dev.adopted = False
        if xtoken:
            dev.xtoken = xtoken
        return dev

    def register_device(self, device_id, account_id, serial, model, name,
                        device_type="speaker"):
        if not device_id:
            return
        d = self._devs.get(device_id)
        if d is None:
            d = self._devs[device_id] = Device(device_id=device_id,
                                               coordinates=self.default_coords)
        if account_id:
            d.account_id = account_id
        if serial:
            d.serial = serial
        if model:
            d.model = model
        d.meta = {**(d.meta or {}), "name": name, "device_type": device_type}

    def devices_for_account(self, account_id):
        if not account_id:
            return []
        return [{"device_id": d.device_id, "serial": d.serial, "model": d.model,
                 "meta": dict(d.meta or {})}
                for d in self._devs.values() if d.account_id == account_id]

    @staticmethod
    def state(dev: Device, bearer: str) -> str:
        if not bearer:
            return "noauth"
        if dev.session_secret and bearer == dev.session_secret:
            return "adopted" if dev.adopted else "ours"
        return "unknown"

    def valid_bearer(self, dev: Device) -> bool:
        return bool(dev.last_bearer) and dev.last_bearer == dev.session_secret

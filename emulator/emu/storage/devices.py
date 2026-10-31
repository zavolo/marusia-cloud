from __future__ import annotations

import os
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession


def _session_ttl() -> int:
    try:
        return int(os.environ.get("MARUSIA_SESSION_TTL", "0") or 0)
    except ValueError:
        return 0

from . import db
from .models import Device, Session
from ..auth import Registry


class MemoryDeviceStore:

    def __init__(self, default_coords: str, strict: bool):
        self._reg = Registry(default_coords, strict)

    @property
    def strict(self) -> bool:
        return self._reg.strict

    async def touch(self, request):
        return self._reg.touch(request)

    async def get(self, device_id: str):
        return self._reg.get(device_id)

    async def ensure(self, device_id: str) -> None:
        self._reg.ensure(device_id)

    async def all(self):
        return self._reg.all()

    async def issue_session(self, dev, xtoken=None):
        return self._reg.issue_session(dev, xtoken)

    async def register_device(self, device_id, account_id, serial, model, name,
                              device_type="speaker"):
        self._reg.register_device(device_id, account_id, serial, model, name, device_type)

    async def devices_for_account(self, account_id):
        return self._reg.devices_for_account(account_id)

    def state(self, dev, bearer: str) -> str:
        return self._reg.state(dev, bearer)

    def valid_bearer(self, dev) -> bool:
        return self._reg.valid_bearer(dev)


@dataclass
class DeviceView:
    device_id: str
    serial: str = ""
    device_ver: str = ""
    device_secret: str = ""
    coordinates: str = ""
    account_id: str | None = None
    session_secret: str | None = None
    session_id: str | None = None
    xtoken: str | None = None
    adopted: bool = False
    last_bearer: str = ""
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


def _view(dev: Device, sess: Session | None, last_bearer: str = "") -> DeviceView:
    return DeviceView(
        device_id=dev.device_id, serial=dev.serial, device_ver=dev.device_ver,
        device_secret=dev.device_secret, coordinates=dev.coordinates,
        account_id=(sess.account_id if sess else dev.account_id),
        session_secret=(sess.session_secret if sess else None),
        session_id=(sess.session_id if sess else None),
        xtoken=(sess.xtoken if sess else None),
        adopted=(sess.adopted if sess else False),
        last_bearer=last_bearer,
        first_seen=dev.first_seen.timestamp() if dev.first_seen else time.time(),
        last_seen=dev.last_seen.timestamp() if dev.last_seen else time.time(),
    )


class PgDeviceStore:
    def __init__(self, default_coords: str, strict: bool):
        self.default_coords = default_coords
        self.strict = strict

    async def _active_session(self, s: AsyncSession, device_id: str) -> Session | None:
        return await s.scalar(
            select(Session).where(
                Session.device_id == device_id,
                or_(Session.expires_at.is_(None), Session.expires_at > func.now()))
            .order_by(Session.last_seen.desc()).limit(1))

    async def _session_by_sid(self, s: AsyncSession, session_id: str) -> Session | None:
        return await s.scalar(
            select(Session).where(
                Session.session_id == session_id,
                or_(Session.expires_at.is_(None), Session.expires_at > func.now()))
            .order_by(Session.last_seen.desc()).limit(1))

    async def touch(self, request) -> tuple[DeviceView, str]:
        dev_id = request.query.get("device_id", "")
        serial = request.query.get("sn", "") or request.query.get("serial", "")
        dver = request.query.get("device_ver", "")
        sid = request.query.get("session_id", "")
        dsecret = request.headers.get("X-Vc-Device-Secret", "")
        bearer = request.headers.get("Authorization", "")
        if bearer.startswith("Bearer "):
            bearer = bearer[7:]

        now = datetime.now(timezone.utc)
        async with db.sessionmaker()() as s, s.begin():
            dev = await s.get(Device, dev_id) if dev_id else None
            if dev is None and dev_id:
                dev = Device(device_id=dev_id, coordinates=self.default_coords,
                             first_seen=now, last_seen=now)
                s.add(dev)
                await s.flush()
            if dev is None:
                return DeviceView(device_id="", last_bearer=bearer), bearer
            if serial:
                dev.serial = serial
            if dver:
                dev.device_ver = dver
            if dsecret:
                dev.device_secret = dsecret
            dev.last_seen = now

            sess = None
            if sid:
                sess = await self._session_by_sid(s, sid)
                if sess is None and not self.strict:
                    sess = Session(session_secret=(bearer or secrets.token_urlsafe(32)),
                                   device_id=dev_id, session_id=sid,
                                   account_id=dev.account_id, adopted=True)
                    s.add(sess)
                    await s.flush()
                elif sess is not None:
                    sess.last_seen = now
                    if not sess.device_id:
                        sess.device_id = dev_id
                    if dev.account_id is None and sess.account_id:
                        dev.account_id = sess.account_id
                    ttl = _session_ttl()
                    if ttl > 0:
                        sess.expires_at = now + timedelta(seconds=ttl)
            if sess is None:
                sess = await self._active_session(s, dev_id)
                if bearer and sess is None and not self.strict:
                    sess = Session(session_secret=bearer, device_id=dev_id,
                                   session_id=uuid.uuid4().hex,
                                   account_id=dev.account_id, adopted=True)
                    s.add(sess)
                    await s.flush()
                elif sess is not None and bearer and sess.session_secret == bearer:
                    sess.last_seen = now
                    ttl = _session_ttl()
                    if ttl > 0:
                        sess.expires_at = now + timedelta(seconds=ttl)
            return _view(dev, sess, bearer), bearer

    async def get(self, device_id: str) -> DeviceView | None:
        async with db.sessionmaker()() as s:
            dev = await s.get(Device, device_id)
            if dev is None:
                return None
            sess = await self._active_session(s, device_id)
            return _view(dev, sess)

    async def ensure(self, device_id: str) -> None:
        if not device_id:
            return
        now = datetime.now(timezone.utc)
        async with db.sessionmaker()() as s, s.begin():
            if await s.get(Device, device_id) is None:
                s.add(Device(device_id=device_id, coordinates=self.default_coords,
                             first_seen=now, last_seen=now))

    async def all(self) -> list[DeviceView]:
        async with db.sessionmaker()() as s:
            devs = (await s.scalars(select(Device))).all()
            out = []
            for dev in devs:
                sess = await self._active_session(s, dev.device_id)
                out.append(_view(dev, sess))
            return out

    async def issue_session(self, dev: DeviceView, xtoken: str | None = None) -> DeviceView:
        new_secret = secrets.token_urlsafe(32)
        new_sid = uuid.uuid4().hex
        ttl = _session_ttl()
        expires = datetime.now(timezone.utc) + timedelta(seconds=ttl) if ttl > 0 else None
        async with db.sessionmaker()() as s, s.begin():
            account_id = dev.account_id or "acc-" + secrets.token_hex(4)
            row = Session(session_secret=new_secret, device_id=dev.device_id,
                          session_id=new_sid, account_id=account_id,
                          xtoken=xtoken, adopted=False, expires_at=expires)
            s.add(row)
            d = await s.get(Device, dev.device_id)
            if d is not None and not d.account_id:
                d.account_id = account_id
        dev.session_secret = new_secret
        dev.session_id = new_sid
        dev.account_id = account_id
        dev.adopted = False
        if xtoken:
            dev.xtoken = xtoken
        return dev

    async def register_device(self, device_id, account_id, serial, model, name,
                              device_type="speaker"):
        if not device_id:
            return
        now = datetime.now(timezone.utc)
        async with db.sessionmaker()() as s, s.begin():
            d = await s.get(Device, device_id)
            if d is None:
                d = Device(device_id=device_id, coordinates=self.default_coords,
                           first_seen=now, last_seen=now)
                s.add(d)
            if account_id:
                d.account_id = account_id
            if serial:
                d.serial = serial
            if model:
                d.model = model
            meta = dict(d.meta or {})
            meta["name"] = name
            meta["device_type"] = device_type
            d.meta = meta

    async def devices_for_account(self, account_id):
        if not account_id:
            return []
        async with db.sessionmaker()() as s:
            rows = (await s.scalars(select(Device).where(Device.account_id == account_id))).all()
            return [{"device_id": d.device_id, "serial": d.serial, "model": d.model,
                     "meta": dict(d.meta or {})} for d in rows]

    @staticmethod
    def state(dev: DeviceView, bearer: str) -> str:
        if not bearer:
            return "noauth"
        if dev.session_secret and bearer == dev.session_secret:
            return "adopted" if dev.adopted else "ours"
        return "unknown"

    def valid_bearer(self, dev: DeviceView) -> bool:
        return bool(dev.last_bearer) and dev.last_bearer == dev.session_secret

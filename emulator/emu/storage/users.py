from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from . import db
from .models import Device, DeviceBinding, PairingCode, User


def hash_password(pw: str, iterations: int = 200000) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt}${dk}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, it, salt, dk = stored.split("$")
        iterations = int(it)
    except (ValueError, AttributeError):
        return False
    if algo != "pbkdf2_sha256":
        return False
    calc = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), iterations).hex()
    return hmac.compare_digest(calc, dk)


def _new_user_id() -> str:
    return "usr-" + secrets.token_hex(6)


def _udict(u: User, device_ids: list[str]) -> dict:
    return {
        "user_id": u.user_id, "username": u.username, "display_name": u.display_name,
        "is_admin": u.is_admin, "disabled": u.disabled,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "devices": device_ids,
    }


class PgUserStore:
    async def create_user(self, username: str, password: str, display_name: str = "",
                          is_admin: bool = False) -> dict:
        uid = _new_user_id()
        async with db.sessionmaker()() as s, s.begin():
            exists = await s.scalar(select(User).where(User.username == username))
            if exists is not None:
                raise ValueError("username already exists")
            s.add(User(user_id=uid, username=username, password_hash=hash_password(password),
                       display_name=display_name or username, is_admin=is_admin))
        return await self.get_user(uid)

    async def get_user(self, user_id: str) -> dict | None:
        async with db.sessionmaker()() as s:
            u = await s.get(User, user_id)
            if u is None:
                return None
            devs = (await s.scalars(
                select(DeviceBinding.device_id).where(DeviceBinding.user_id == user_id))).all()
            return _udict(u, list(devs))

    async def get_by_username(self, username: str) -> dict | None:
        async with db.sessionmaker()() as s:
            u = await s.scalar(select(User).where(User.username == username))
            if u is None:
                return None
            devs = (await s.scalars(
                select(DeviceBinding.device_id).where(DeviceBinding.user_id == u.user_id))).all()
            return _udict(u, list(devs))

    async def verify(self, username: str, password: str) -> dict | None:
        async with db.sessionmaker()() as s:
            u = await s.scalar(select(User).where(User.username == username))
            if u is None or u.disabled or not verify_password(password, u.password_hash):
                return None
            return _udict(u, [])

    async def set_password(self, user_id: str, password: str) -> bool:
        async with db.sessionmaker()() as s, s.begin():
            u = await s.get(User, user_id)
            if u is None:
                return False
            u.password_hash = hash_password(password)
            return True

    async def set_disabled(self, user_id: str, disabled: bool) -> bool:
        async with db.sessionmaker()() as s, s.begin():
            u = await s.get(User, user_id)
            if u is None:
                return False
            u.disabled = disabled
            return True

    async def list_users(self) -> list[dict]:
        async with db.sessionmaker()() as s:
            users = (await s.scalars(select(User).order_by(User.created_at))).all()
            binds = (await s.execute(select(DeviceBinding.user_id, DeviceBinding.device_id))).all()
        by_user: dict[str, list[str]] = {}
        for uid, did in binds:
            by_user.setdefault(uid, []).append(did)
        return [_udict(u, by_user.get(u.user_id, [])) for u in users]

    async def delete_user(self, user_id: str) -> bool:
        async with db.sessionmaker()() as s, s.begin():
            u = await s.get(User, user_id)
            if u is None:
                return False
            await s.execute(delete(DeviceBinding).where(DeviceBinding.user_id == user_id))
            await s.execute(delete(PairingCode).where(PairingCode.user_id == user_id))
            await s.delete(u)
            return True

    async def bind_device(self, device_id: str, user_id: str, source: str = "admin") -> bool:
        async with db.sessionmaker()() as s, s.begin():
            u = await s.get(User, user_id)
            if u is None:
                return False
            dev = await s.get(Device, device_id)
            if dev is None:
                dev = Device(device_id=device_id, account_id=user_id)
                s.add(dev)
                await s.flush()
            row = await s.get(DeviceBinding, device_id)
            if row is None:
                s.add(DeviceBinding(device_id=device_id, user_id=user_id, source=source))
            else:
                row.user_id = user_id
                row.source = source
            dev.account_id = user_id
            return True

    async def unbind_device(self, device_id: str) -> bool:
        async with db.sessionmaker()() as s, s.begin():
            row = await s.get(DeviceBinding, device_id)
            if row is None:
                return False
            await s.delete(row)
            dev = await s.get(Device, device_id)
            if dev is not None:
                dev.account_id = None
            return True

    async def devices_for(self, user_id: str) -> list[str]:
        async with db.sessionmaker()() as s:
            return list((await s.scalars(
                select(DeviceBinding.device_id).where(DeviceBinding.user_id == user_id))).all())

    async def account_for_device(self, device_id: str) -> str | None:
        async with db.sessionmaker()() as s:
            return await s.scalar(
                select(DeviceBinding.user_id).where(DeviceBinding.device_id == device_id))

    async def create_pair_code(self, user_id: str, ttl: int = 600) -> str:
        code = f"{secrets.randbelow(1000000):06d}"
        expires = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        async with db.sessionmaker()() as s, s.begin():
            s.add(PairingCode(code=code, user_id=user_id, expires_at=expires))
        return code

    async def redeem_pair_code(self, code: str, device_id: str) -> str | None:
        async with db.sessionmaker()() as s, s.begin():
            row = await s.get(PairingCode, code)
            if row is None or row.used:
                return None
            if row.expires_at is not None and row.expires_at < datetime.now(timezone.utc):
                return None
            row.used = True
        await self.bind_device(device_id, row.user_id, source="pair")
        return row.user_id


class MemoryUserStore:
    def __init__(self):
        self._users: dict[str, dict] = {}
        self._by_name: dict[str, str] = {}
        self._bind: dict[str, str] = {}
        self._codes: dict[str, dict] = {}

    async def create_user(self, username, password, display_name="", is_admin=False):
        if username in self._by_name:
            raise ValueError("username already exists")
        uid = _new_user_id()
        self._users[uid] = {
            "user_id": uid, "username": username,
            "password_hash": hash_password(password),
            "display_name": display_name or username, "is_admin": is_admin,
            "disabled": False, "created_at": datetime.now(timezone.utc).isoformat()}
        self._by_name[username] = uid
        return await self.get_user(uid)

    def _dev_ids(self, uid):
        return [d for d, u in self._bind.items() if u == uid]

    async def get_user(self, user_id):
        u = self._users.get(user_id)
        if not u:
            return None
        return {**{k: v for k, v in u.items() if k != "password_hash"},
                "devices": self._dev_ids(user_id)}

    async def get_by_username(self, username):
        uid = self._by_name.get(username)
        return await self.get_user(uid) if uid else None

    async def verify(self, username, password):
        uid = self._by_name.get(username)
        u = self._users.get(uid) if uid else None
        if not u or u["disabled"] or not verify_password(password, u["password_hash"]):
            return None
        return await self.get_user(uid)

    async def set_password(self, user_id, password):
        u = self._users.get(user_id)
        if not u:
            return False
        u["password_hash"] = hash_password(password)
        return True

    async def set_disabled(self, user_id, disabled):
        u = self._users.get(user_id)
        if not u:
            return False
        u["disabled"] = disabled
        return True

    async def list_users(self):
        return [await self.get_user(uid) for uid in self._users]

    async def delete_user(self, user_id):
        u = self._users.pop(user_id, None)
        if not u:
            return False
        self._by_name.pop(u["username"], None)
        for d in [d for d, uid in self._bind.items() if uid == user_id]:
            self._bind.pop(d, None)
        for c in [c for c, v in self._codes.items() if v["user_id"] == user_id]:
            self._codes.pop(c, None)
        return True

    async def bind_device(self, device_id, user_id, source="admin"):
        if user_id not in self._users:
            return False
        self._bind[device_id] = user_id
        return True

    async def unbind_device(self, device_id):
        return self._bind.pop(device_id, None) is not None

    async def devices_for(self, user_id):
        return self._dev_ids(user_id)

    async def account_for_device(self, device_id):
        return self._bind.get(device_id)

    async def create_pair_code(self, user_id, ttl=600):
        code = f"{secrets.randbelow(1000000):06d}"
        self._codes[code] = {"user_id": user_id,
                             "expires_at": datetime.now(timezone.utc) + timedelta(seconds=ttl),
                             "used": False}
        return code

    async def redeem_pair_code(self, code, device_id):
        row = self._codes.get(code)
        if not row or row["used"] or row["expires_at"] < datetime.now(timezone.utc):
            return None
        row["used"] = True
        await self.bind_device(device_id, row["user_id"], source="pair")
        return row["user_id"]

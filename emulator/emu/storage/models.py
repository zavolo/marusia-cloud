from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Device(Base):
    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(Text, primary_key=True)
    serial: Mapped[str] = mapped_column(Text, default="", server_default="")
    device_ver: Mapped[str] = mapped_column(Text, default="", server_default="")
    device_secret: Mapped[str] = mapped_column(Text, default="", server_default="")
    account_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    coordinates: Mapped[str] = mapped_column(Text, default="", server_default="")
    model: Mapped[str] = mapped_column(Text, default="", server_default="")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    sessions: Mapped[list["Session"]] = relationship(
        back_populates="device", cascade="all, delete-orphan")
    alarms: Mapped[list["Alarm"]] = relationship(
        back_populates="device", cascade="all, delete-orphan")
    settings: Mapped[list["DeviceSetting"]] = relationship(
        back_populates="device", cascade="all, delete-orphan")


class Session(Base):
    __tablename__ = "sessions"

    session_secret: Mapped[str] = mapped_column(Text, primary_key=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.device_id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(Text, index=True)
    account_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    xtoken: Mapped[str | None] = mapped_column(Text, nullable=True)
    adopted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    device: Mapped[Device] = relationship(back_populates="sessions")


class Alarm(Base):
    __tablename__ = "alarms"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.device_id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    alarm_time: Mapped[int] = mapped_column(Integer)
    weekdays: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), default=list, server_default="{}")
    first_time: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    music_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    device: Mapped[Device] = relationship(back_populates="alarms")


class DeviceSetting(Base):
    __tablename__ = "device_settings"

    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.device_id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    device: Mapped[Device] = relationship(back_populates="settings")


class TtsCache(Base):
    __tablename__ = "tts_cache"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    encoder: Mapped[str] = mapped_column(Text, default="opus", server_default="opus")
    voice: Mapped[str] = mapped_column(Text, default="", server_default="")
    content_type: Mapped[str] = mapped_column(Text, default="audio/ogg", server_default="audio/ogg")
    blob_path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    hits: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class EventLog(Base):
    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(Text)
    phrase_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class SkillRow(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, default="", server_default="")
    category: Mapped[str] = mapped_column(Text, default="other", server_default="other")
    kind: Mapped[str] = mapped_column(Text, default="say", server_default="say")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    icon: Mapped[str] = mapped_column(Text, default="", server_default="")
    match: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    responses: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    commands: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    examples: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    response_pool: Mapped[list["SkillResponse"]] = relationship(
        back_populates="skill", cascade="all, delete-orphan")


class SkillResponse(Base):
    __tablename__ = "skill_responses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    skill: Mapped[SkillRow] = relationship(back_populates="response_pool")


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    username: Mapped[str] = mapped_column(Text, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text, default="", server_default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bindings: Mapped[list["DeviceBinding"]] = relationship(
        back_populates="user", cascade="all, delete-orphan")


class DeviceBinding(Base):
    __tablename__ = "device_bindings"

    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.device_id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(Text, default="admin", server_default="admin")
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="bindings")


class PairingCode(Base):
    __tablename__ = "pairing_codes"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_jsonb_empty = sa.text("'{}'::jsonb")
_arr_empty = sa.text("'{}'::integer[]")


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("device_id", sa.Text(), primary_key=True),
        sa.Column("serial", sa.Text(), nullable=False, server_default=""),
        sa.Column("device_ver", sa.Text(), nullable=False, server_default=""),
        sa.Column("device_secret", sa.Text(), nullable=False, server_default=""),
        sa.Column("account_id", sa.Text(), nullable=True),
        sa.Column("coordinates", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.Text(), nullable=False, server_default=""),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("meta", postgresql.JSONB(), nullable=False, server_default=_jsonb_empty),
    )

    op.create_table(
        "sessions",
        sa.Column("session_secret", sa.Text(), primary_key=True),
        sa.Column("device_id", sa.Text(), sa.ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=True),
        sa.Column("xtoken", sa.Text(), nullable=True),
        sa.Column("adopted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sessions_device_id", "sessions", ["device_id"])
    op.create_index("ix_sessions_last_seen", "sessions", ["last_seen"])

    op.create_table(
        "alarms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("device_id", sa.Text(), sa.ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("alarm_time", sa.Integer(), nullable=False),
        sa.Column("weekdays", postgresql.ARRAY(sa.Integer()), nullable=False, server_default=_arr_empty),
        sa.Column("first_time", sa.BigInteger(), nullable=True),
        sa.Column("music_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_alarms_device_id", "alarms", ["device_id"])

    op.create_table(
        "device_settings",
        sa.Column("device_id", sa.Text(), sa.ForeignKey("devices.device_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False, server_default=_jsonb_empty),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "tts_cache",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("encoder", sa.Text(), nullable=False, server_default="opus"),
        sa.Column("voice", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_type", sa.Text(), nullable=False, server_default="audio/ogg"),
        sa.Column("blob_path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "event_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("device_id", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("phrase_id", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=_jsonb_empty),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_event_log_device", "event_log", ["device_id", sa.text("created_at DESC")])
    op.create_index("ix_event_log_kind", "event_log", ["kind", sa.text("created_at DESC")])

    op.create_table(
        "commands",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("command", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_commands_device", "commands", ["device_id", sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_table("commands")
    op.drop_table("event_log")
    op.drop_table("tts_cache")
    op.drop_table("device_settings")
    op.drop_table("alarms")
    op.drop_table("sessions")
    op.drop_table("devices")

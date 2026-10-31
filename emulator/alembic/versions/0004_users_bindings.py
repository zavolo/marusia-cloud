from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.Text(), primary_key=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "device_bindings",
        sa.Column("device_id", sa.Text(), sa.ForeignKey("devices.device_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default="admin"),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_device_bindings_user", "device_bindings", ["user_id"])

    op.create_table(
        "pairing_codes",
        sa.Column("code", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_pairing_codes_user", "pairing_codes", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_pairing_codes_user", table_name="pairing_codes")
    op.drop_table("pairing_codes")
    op.drop_index("ix_device_bindings_user", table_name="device_bindings")
    op.drop_table("device_bindings")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")

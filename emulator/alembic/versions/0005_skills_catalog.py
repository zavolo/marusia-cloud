from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.Text(), nullable=False, server_default="other"),
        sa.Column("kind", sa.Text(), nullable=False, server_default="say"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("icon", sa.Text(), nullable=False, server_default=""),
        sa.Column("match", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("responses", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("commands", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("examples", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "skill_responses",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("skill_id", sa.Text(), sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_skill_responses_skill", "skill_responses", ["skill_id"])


def downgrade() -> None:
    op.drop_index("ix_skill_responses_skill", table_name="skill_responses")
    op.drop_table("skill_responses")
    op.drop_table("skills")

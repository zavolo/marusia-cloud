from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_sessions_session_id", "sessions", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_session_id", table_name="sessions")

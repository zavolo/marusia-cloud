from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tts_cache",
                  sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_tts_cache_last_hit", "tts_cache", ["last_hit_at"])


def downgrade() -> None:
    op.drop_index("ix_tts_cache_last_hit", table_name="tts_cache")
    op.drop_column("tts_cache", "last_hit_at")

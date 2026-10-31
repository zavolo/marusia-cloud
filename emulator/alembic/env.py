from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from emu.storage.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = os.environ.get("MARUSIA_DATABASE_URL")
    if not url:
        raise RuntimeError("MARUSIA_DATABASE_URL is required to run migrations")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(), target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
                      compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url(), pool_pre_ping=True)
    async with engine.connect() as conn:
        await conn.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

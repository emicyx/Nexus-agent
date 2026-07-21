"""Alembic 环境配置 - async 版本

从 app.db.session 读取 engine，从 app.models 读取 metadata。
"""
import asyncio
import logging
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# 确保 backend/ 在 sys.path，使 app.* 可导入
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from app.config import settings  # noqa: E402
from app.db.session import _make_dsn  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config

# 注入 DSN
config.set_main_option("sqlalchemy.url", _make_dsn(settings.POSTGRES_DSN))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

logger = logging.getLogger("alembic")

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _make_dsn(settings.POSTGRES_DSN)
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

"""PostgreSQL 异步 Session 工厂（Week 3 实现）

使用 SQLAlchemy 2.0 async ORM + asyncpg 驱动。
- engine / AsyncSessionLocal：全局引擎与会话工厂
- get_db()：FastAPI 异步依赖，yield AsyncSession
- init_db()：开发期快速建表（metadata.create_all），正式迁移用 Alembic
"""
import logging

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

logger = logging.getLogger("db")


def _make_dsn(dsn: str) -> str:
    """将 psycopg2 风格 DSN 转为 asyncpg 风格。

    asyncpg 驱动要求 postgresql+asyncpg:// 前缀；
    若用户在 .env 写的是 postgresql://，这里自动补 +asyncpg。
    """
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    return dsn


engine = create_async_engine(
    _make_dsn(settings.POSTGRES_DSN),
    pool_pre_ping=True,
    echo=False,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncSession:
    """FastAPI 依赖：提供异步 Session，请求结束自动关闭。"""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """开发期建表：直接用 metadata.create_all 快速建表。

    生产环境应使用 Alembic 迁移；首次用 Alembic 时需 `alembic stamp head`
    对齐已存在的表。
    """
    from app.models import Base  # noqa: WPS433 - 延迟导入避免循环依赖

    async with engine.begin() as conn:
        # pgvector 扩展兜底（init.sql 未生效时也保证可用）
        await conn.execute(sa_text("CREATE EXTENSION IF NOT EXISTS vector;"))
        await conn.run_sync(Base.metadata.create_all)
    logger.info("init_db: tables ensured (create_all + vector ext)")

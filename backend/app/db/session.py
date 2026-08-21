"""PostgreSQL 异步 Session 工厂（Week 3 实现）

使用 SQLAlchemy 2.0 async ORM + asyncpg 驱动。
- engine / AsyncSessionLocal：全局引擎与会话工厂
- get_db()：FastAPI 异步依赖，yield AsyncSession
- init_db()：开发期快速建表（metadata.create_all），正式迁移用 Alembic
- get_sync_session()：同步 Session 工厂（CrewAI 工具 _run / 后台线程使用，
  历史上 rag_search_tool / kb_ingest_tool / memory_ltm 各自维护过一份，
  现统一收敛到此处，避免多个独立连接池）
"""
import logging
import threading

from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

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
    # 显式连接池参数（默认 5+10 偏小且不回收长连接）：
    # API 层并发 + SSE 长请求下取 10+20；半小时回收防防火墙/DB 侧静默断连
    # （pre_ping 已兜底，recycle 双保险）。
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800,
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


# ---------- 同步 Session 工厂（CrewAI 工具 / 后台线程） ----------

_sync_engine = None
_SyncSessionLocal: sessionmaker | None = None
# 惰性初始化锁：多个 worker 线程（LTM 提取 / kb_ingest / rag_search /
# STM 摘要）并发首调时，无锁会创建多个 engine，被覆盖的旧 engine
# 连接池泄漏。双检 + 模块级锁。
_sync_init_lock = threading.Lock()


def _make_sync_dsn(dsn: str) -> str:
    """将 asyncpg 风格 DSN 转为 psycopg2 风格（postgresql://）。"""
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    return dsn


def get_sync_session() -> Session:
    """同步 Session（psycopg2），延迟初始化避免启动时连不上 DB。

    CrewAI akickoff() 在主事件循环调用工具 _run，不能用 asyncio；
    后台线程（LTM 提取等）同理。全项目唯一的同步 engine。
    """
    global _sync_engine, _SyncSessionLocal
    if _SyncSessionLocal is None:
        with _sync_init_lock:
            if _SyncSessionLocal is None:
                _sync_engine = create_engine(
                    _make_sync_dsn(settings.POSTGRES_DSN),
                    pool_pre_ping=True,
                    future=True,
                    # worker 线程并发低于 API 层，取默认量级并显式声明：
                    # 5 常驻 + 10 溢出，半小时回收。
                    pool_size=5,
                    max_overflow=10,
                    pool_timeout=30,
                    pool_recycle=1800,
                )
                _SyncSessionLocal = sessionmaker(
                    bind=_sync_engine, expire_on_commit=False
                )
    return _SyncSessionLocal()


async def dispose_engines() -> None:
    """优雅停机（B4）：释放异步与同步引擎的连接池。"""
    global _sync_engine, _SyncSessionLocal
    await engine.dispose()
    logger.info("async engine disposed")
    with _sync_init_lock:
        if _sync_engine is not None:
            _sync_engine.dispose()
            _sync_engine = None
            _SyncSessionLocal = None
            logger.info("sync engine disposed")


def vec_to_sql_literal(vec: list[float]) -> str:
    """把向量列表格式化为 pgvector 接受的字符串字面量（裸 SQL 参数用）。"""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"

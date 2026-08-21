"""Redis 客户端（同步 + 异步双客户端）

- 异步客户端：FastAPI API 层使用（如审批接口）
- 同步客户端：CrewAI 工具 _run 内使用（CrewAI akickoff 在主事件循环调用工具，不能用 asyncio）

Week 5 HITL：审批状态机存储
"""
import json
import logging
import time
from typing import Any

import redis
import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger("redis")

# 网络超时：redis-py 默认 socket_timeout=None（无限等待）。Redis 挂起时
# HITL 审批轮询 / token 镜像写入所在的 worker 线程会永久阻塞（优雅停机的
# 强制取消也无法中断线程），必须显式设超时让调用以异常形式返回。
_REDIS_CONNECT_TIMEOUT = 5  # 建连超时（秒）
_REDIS_SOCKET_TIMEOUT = 10  # 单次读写超时（秒）
_REDIS_HEALTH_CHECK_INTERVAL = 30  # 空闲连接周期性 PING，及时发现半死连接
_REDIS_MAX_CONNECTIONS = 50  # 连接池上限，防连接无界增长

# 异步客户端（API 层）
_async_client: aioredis.Redis | None = None

# 同步客户端（工具层）
_sync_client: redis.Redis | None = None


def get_async_redis() -> aioredis.Redis:
    """异步 Redis 客户端（FastAPI 路由层使用）。"""
    global _async_client
    if _async_client is None:
        _async_client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=_REDIS_CONNECT_TIMEOUT,
            socket_timeout=_REDIS_SOCKET_TIMEOUT,
            health_check_interval=_REDIS_HEALTH_CHECK_INTERVAL,
            max_connections=_REDIS_MAX_CONNECTIONS,
        )
        logger.info("async redis client created")
    return _async_client


def get_sync_redis() -> redis.Redis:
    """同步 Redis 客户端（CrewAI 工具 _run 内使用，避开 asyncio）。"""
    global _sync_client
    if _sync_client is None:
        _sync_client = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=_REDIS_CONNECT_TIMEOUT,
            socket_timeout=_REDIS_SOCKET_TIMEOUT,
            health_check_interval=_REDIS_HEALTH_CHECK_INTERVAL,
            max_connections=_REDIS_MAX_CONNECTIONS,
        )
        logger.info("sync redis client created")
    return _sync_client


# ---------- 优雅停机（B4）：关闭连接池 ----------


async def close_async_redis() -> None:
    global _async_client
    if _async_client is not None:
        try:
            await _async_client.aclose()
        except Exception:  # noqa: BLE001
            logger.debug("close async redis failed", exc_info=True)
        _async_client = None
        logger.info("async redis client closed")


def close_sync_redis() -> None:
    global _sync_client
    if _sync_client is not None:
        try:
            _sync_client.close()
        except Exception:  # noqa: BLE001
            logger.debug("close sync redis failed", exc_info=True)
        _sync_client = None
        logger.info("sync redis client closed")


# ---------- 审批状态机辅助 ----------

APPROVAL_KEY_PREFIX = "approval:"
APPROVAL_TTL = 300  # 5 分钟兜底（前端超时 120s）


def approval_key(approval_id: str) -> str:
    return f"{APPROVAL_KEY_PREFIX}{approval_id}"


def set_approval_pending_sync(
    approval_id: str,
    data: dict[str, Any],
) -> None:
    """写入 PENDING 状态（同步，工具层用）。"""
    r = get_sync_redis()
    r.set(approval_key(approval_id), json.dumps(data), ex=APPROVAL_TTL)


def get_approval_sync(approval_id: str) -> dict[str, Any] | None:
    """同步读取审批状态。"""
    r = get_sync_redis()
    raw = r.get(approval_key(approval_id))
    if raw is None:
        return None
    return json.loads(raw)


def update_approval_sync(
    approval_id: str,
    status: str,
    comment: str = "",
) -> bool:
    """同步更新审批状态。返回 True=成功，False=approval 不存在。"""
    r = get_sync_redis()
    raw = r.get(approval_key(approval_id))
    if raw is None:
        return False
    data: dict[str, Any] = json.loads(raw)
    data["status"] = status
    data["comment"] = comment
    data["resolved_at"] = time.time()
    r.set(approval_key(approval_id), json.dumps(data), ex=APPROVAL_TTL)
    return True


async def update_approval(
    approval_id: str,
    status: str,
    comment: str = "",
) -> bool:
    """异步更新审批状态（API 层使用，避免 async 路由内调用同步 Redis 阻塞事件循环）。"""
    r = get_async_redis()
    raw = await r.get(approval_key(approval_id))
    if raw is None:
        return False
    data: dict[str, Any] = json.loads(raw)
    data["status"] = status
    data["comment"] = comment
    data["resolved_at"] = time.time()
    await r.set(approval_key(approval_id), json.dumps(data), ex=APPROVAL_TTL)
    return True


"""聊天接口限流（P0-5）。

背景：全站此前没有任何频率/并发限制，`/v1/chat/stream` 每个请求起一个
Crew + worker 线程 + SSE 队列，持密钥者（或代理场景下的任意浏览器访客）
可并发打满，构成成本型 DoS（LLM 按 token 计费）。

方案（两层，均为单配置开关、0=关闭）：
- CHAT_RATE_LIMIT_PER_MIN：固定窗口计数。Redis INCR（跨进程、重启不清零，
  与 token 预算同实例），Redis 不可用时退回进程内存计数。按调用方维度
  （X-API-Key 优先，否则客户端 IP）隔离。
- MAX_CONCURRENT_RUNS：在跑 SSE 运行数上限（run_control 注册表实时计数），
  超过直接 503，见 chat.py 调用点。

固定窗口精度足够（防御成本 DoS 不需要平滑窗口），实现零新依赖。
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict

from fastapi import HTTPException, Request

from app.config import settings

logger = logging.getLogger("rate_limit")

_KEY_PREFIX = "ratelimit:chat:"
_REDIS_TTL = 120  # 略大于 60s 窗口，跨窗口自然过期
_MEM_CACHE_LIMIT = 10_000  # 内存兜底计数键上限（防无界）

_mem_lock = threading.Lock()
_mem_counters: OrderedDict[str, int] = OrderedDict()


def _client_key(request: Request) -> str:
    """调用方标识：X-API-Key 优先（同密钥共享配额），否则客户端 IP。"""
    api_key = request.headers.get("x-api-key")
    if api_key:
        return f"key:{api_key[:16]}"
    client = request.client.host if request.client else "unknown"
    return f"ip:{client}"


def _current_window() -> int:
    return int(time.time() // 60)


async def _redis_incr(key: str) -> int | None:
    """Redis 计数，失败/不可用返回 None（调用方走内存兜底）。"""
    try:
        from app.db.redis import get_async_redis

        r = get_async_redis()
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, _REDIS_TTL)
        return int(count)
    except Exception:  # noqa: BLE001 - Redis 故障降级内存计数
        return None


def _memory_incr(key: str) -> int:
    with _mem_lock:
        _mem_counters[key] = _mem_counters.get(key, 0) + 1
        _mem_counters.move_to_end(key)
        while len(_mem_counters) > _MEM_CACHE_LIMIT:
            _mem_counters.popitem(last=False)
        return _mem_counters[key]


def reset_for_tests() -> None:
    with _mem_lock:
        _mem_counters.clear()


async def check_chat_rate_limit(request: Request) -> None:
    """聊天流式接口限流检查。超限抛 429（调用方维度、每分钟窗口）。

    在 chat_stream 入口、启动 producer 之前调用。
    """
    limit = settings.CHAT_RATE_LIMIT_PER_MIN
    if limit <= 0:
        return
    key = f"{_KEY_PREFIX}{_client_key(request)}:{_current_window()}"
    try:
        count = await _redis_incr(key)
    except Exception:  # noqa: BLE001 - 双保险：计数路径任何异常都不阻断请求判定
        count = None
    if count is None:
        count = _memory_incr(key)
    if count > limit:
        logger.warning(
            "rate_limit: chat 请求超限（%s 本分钟第 %d 次 / 上限 %d）",
            _client_key(request), count, limit,
        )
        raise HTTPException(
            status_code=429,
            detail=f"请求过于频繁：每分钟最多 {limit} 次对话，请稍后再试。",
        )

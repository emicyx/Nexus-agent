"""Token 用量计量与日预算熔断（上线欠账 A2）。

背景：每个 LLM 请求都在花钱，但全库没有任何用量记录与闸门——
API 响应里的 usage 字段一直被丢弃（非流式未读；流式甚至没向
DashScope 请求 include_usage）。

方案：
- 计量：四条 LLM 调用路径（同步/异步 × 流式/非流式）统一把响应中的
  usage 交给本模块累计。内存计数（线程安全）保证进程内准确，
  Redis 按「天」INCR 镜像（跨重启、多进程可见），Redis 不可用时降级
  仅内存——计量失败绝不影响正常调用。
- 熔断：LLM_TOKEN_DAILY_BUDGET > 0 时，新请求开始前检查当日累计，
  超限抛 TokenBudgetExceededError → 走既有错误分类管道（budget_exceeded
  类）→ SSE error 事件 → 前端友好提示。进行中的会话允许跑完当前轮。

会话归集：chat producer 用 contextvar 绑定 session_uuid，
worker 线程（asyncio.to_thread 复制 contextvars）自动继承，
供 /metrics 输出每会话消耗。
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
from collections import OrderedDict
from datetime import date

from app.config import settings

logger = logging.getLogger("token_budget")

_REDIS_KEY_PREFIX = "token_usage:day:"
_REDIS_TTL_SECONDS = 2 * 86400  # 保留 2 天，跨日自然滚动
_SESSION_TRACK_LIMIT = 200  # /metrics 会话归集上限（防内存无界）
_WARN_THRESHOLD_PCT = 80  # 预算消耗到该比例时告警一次


class TokenBudgetExceededError(Exception):
    """当日 Token 预算已用完（A2 熔断）。分类见 core/llm_errors.py budget_exceeded。"""

    def __init__(self, budget: int, used: int):
        self.budget = budget
        self.used = used
        super().__init__(
            f"当日 Token 预算已用完：已消耗 {used} / 预算 {budget}。"
            "请明日再试，或调大 LLM_TOKEN_DAILY_BUDGET。"
        )


class BudgetExceededRunError(BaseException):
    """运行中途预算超限的穿透载体（worker 线程检查点抛出）。

    继承 BaseException 的原因与 RunCancelledError 相同：CrewAI 的工具异常
    兜底（except Exception）会把普通异常转成"工具报错"字符串结果，Agent
    会继续跑；只有 BaseException 能穿透执行栈直达 producer。
    producer 需显式捕获并转发 .original 走错误分类管道。
    """

    def __init__(self, original: TokenBudgetExceededError):
        super().__init__(str(original))
        self.original = original


# 当前请求归属的会话（chat producer 绑定；None = 后台任务不归集到会话）
_token_session_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "token_session", default=None
)


def bind_token_session(session: str | None) -> contextvars.Token:
    return _token_session_ctx.set(session)


def unbind_token_session(token: contextvars.Token) -> None:
    _token_session_ctx.reset(token)


_lock = threading.Lock()
_daily_totals: dict[str, int] = {}  # "YYYY-MM-DD" -> tokens
_session_totals: OrderedDict[str, int] = OrderedDict()
_warned_dates: set[str] = set()


def _today() -> str:
    return date.today().isoformat()


def _memory_add(day: str, total: int, session: str | None) -> None:
    with _lock:
        _daily_totals[day] = _daily_totals.get(day, 0) + total
        if session:
            _session_totals[session] = _session_totals.get(session, 0) + total
            _session_totals.move_to_end(session)
            while len(_session_totals) > _SESSION_TRACK_LIMIT:
                _session_totals.popitem(last=False)


def _check_threshold(day: str) -> None:
    budget = settings.LLM_TOKEN_DAILY_BUDGET
    if budget <= 0:
        return
    with _lock:
        used = _daily_totals.get(day, 0)
        if day in _warned_dates:
            return
        if used >= budget * _WARN_THRESHOLD_PCT / 100:
            _warned_dates.add(day)
        else:
            return
    logger.warning(
        "token_budget: 当日已消耗 %d / %d (%.0f%%)",
        used, budget, used * 100 / budget,
    )


def _redis_key(day: str) -> str:
    return f"{_REDIS_KEY_PREFIX}{day}"


def _redis_mirror_sync(day: str, total: int) -> None:
    try:
        from app.db.redis import get_sync_redis

        r = get_sync_redis()
        pipe = r.pipeline()
        pipe.incrby(_redis_key(day), total)
        pipe.expire(_redis_key(day), _REDIS_TTL_SECONDS)
        pipe.execute()
    except Exception:  # noqa: BLE001 - Redis 故障降级为仅内存计量
        logger.debug("token_budget: redis mirror 失败（仅内存计数）", exc_info=True)


async def _redis_mirror_async(day: str, total: int) -> None:
    try:
        from app.db.redis import get_async_redis

        r = get_async_redis()
        pipe = r.pipeline()
        await pipe.incrby(_redis_key(day), total)
        await pipe.expire(_redis_key(day), _REDIS_TTL_SECONDS)
        await pipe.execute()
    except Exception:  # noqa: BLE001
        logger.debug("token_budget: redis mirror 失败（仅内存计数）", exc_info=True)


def record_usage(
    prompt_tokens: int | None = 0,
    completion_tokens: int | None = 0,
    total_tokens: int | None = None,
) -> None:
    """累计一次 LLM 调用的用量（同步路径，可在 worker 线程调用）。绝不抛异常。"""
    try:
        p = int(prompt_tokens or 0)
        c = int(completion_tokens or 0)
        t = int(total_tokens) if total_tokens else p + c
        if t <= 0:
            return
        day = _today()
        session = _token_session_ctx.get()
        _memory_add(day, t, session)
        _check_threshold(day)
        _redis_mirror_sync(day, t)
    except Exception:  # noqa: BLE001 - 计量故障不影响业务
        logger.debug("token_budget: record_usage 异常", exc_info=True)


async def arecord_usage(
    prompt_tokens: int | None = 0,
    completion_tokens: int | None = 0,
    total_tokens: int | None = None,
) -> None:
    """同 record_usage，事件循环上的异步路径用（Redis 镜像走 async 客户端）。"""
    try:
        p = int(prompt_tokens or 0)
        c = int(completion_tokens or 0)
        t = int(total_tokens) if total_tokens else p + c
        if t <= 0:
            return
        day = _today()
        session = _token_session_ctx.get()
        _memory_add(day, t, session)
        _check_threshold(day)
        await _redis_mirror_async(day, t)
    except Exception:  # noqa: BLE001
        logger.debug("token_budget: arecord_usage 异常", exc_info=True)


def extract_usage(result: dict) -> tuple[int, int, int]:
    """从 DashScope 响应 dict 中取 (prompt, completion, total)。缺字段返回 0。"""
    usage = result.get("usage") or {}
    return (
        usage.get("prompt_tokens") or 0,
        usage.get("completion_tokens") or 0,
        usage.get("total_tokens") or 0,
    )


def record_result_usage(result: dict) -> None:
    """从完整响应 dict 计量（同步路径便捷入口）。"""
    p, c, t = extract_usage(result)
    record_usage(p, c, t)


async def arecord_result_usage(result: dict) -> None:
    p, c, t = extract_usage(result)
    await arecord_usage(p, c, t)


async def ensure_budget_available() -> None:
    """新请求开始前检查当日预算。超限抛 TokenBudgetExceededError。

    Redis 值优先（跨重启持久）；Redis 不可用时退回进程内存计数。
    """
    budget = settings.LLM_TOKEN_DAILY_BUDGET
    if budget <= 0:
        return
    day = _today()
    used: int | None = None
    try:
        from app.db.redis import get_async_redis

        r = get_async_redis()
        raw = await r.get(_redis_key(day))
        if raw is not None:
            used = int(raw)
    except Exception:  # noqa: BLE001
        used = None
    if used is None:
        with _lock:
            used = _daily_totals.get(day, 0)
    if used >= budget:
        raise TokenBudgetExceededError(budget, used)


# ── 运行中途预算检查点 ─────────────────────────────────────────────


_checkpoint_min_interval = 30.0  # 秒：节流，避免每次工具调用都打 Redis
_last_checkpoint = 0.0


def reset_checkpoint_throttle_for_tests() -> None:
    global _last_checkpoint
    _last_checkpoint = 0.0


def check_budget_checkpoint(force: bool = False) -> None:
    """运行中途的预算检查点（worker 线程内调用，tool_events 每次工具调用前触发）。

    ensure_budget_available 只在请求开始检查一次，"进行中的会话可跑完当前轮"
    意味着一个多任务 Crew 的长跑轮可以远超预算线。本检查点在每次工具调用
    边界（节流 30s）复查当日累计，超限抛 TokenBudgetExceededError，
    经 producer 的错误分类管道返回 budget_exceeded——把超支上限从
    "整个会话轮" 收敛到 "单次工具间隔"。

    force=True 跳过节流（测试用）。
    """
    global _last_checkpoint
    budget = settings.LLM_TOKEN_DAILY_BUDGET
    if budget <= 0:
        return
    now = time.monotonic()
    if not force and now - _last_checkpoint < _checkpoint_min_interval:
        return
    _last_checkpoint = now
    day = _today()
    used: int | None = None
    try:
        from app.db.redis import get_sync_redis

        r = get_sync_redis()
        raw = r.get(_redis_key(day))
        if raw is not None:
            used = int(raw)
    except Exception:  # noqa: BLE001 - Redis 故障退回内存计数
        used = None
    if used is None:
        with _lock:
            used = _daily_totals.get(day, 0)
    if used >= budget:
        # BaseException 载体穿透 CrewAI 工具异常兜底（见类注释）
        raise BudgetExceededRunError(TokenBudgetExceededError(budget, used))


# ── /metrics 读取接口 ─────────────────────────────────────────────


def get_today_total() -> int:
    with _lock:
        return _daily_totals.get(_today(), 0)


def get_budget() -> int:
    return settings.LLM_TOKEN_DAILY_BUDGET


def get_session_totals() -> dict[str, int]:
    with _lock:
        return dict(_session_totals)


def reset_for_tests() -> None:
    with _lock:
        _daily_totals.clear()
        _session_totals.clear()
        _warned_dates.clear()

"""运行控制（上线欠账 A1 + B4 共用基建）。

背景：SSE 断连时只 cancel 了 producer task。asyncio 的 cancel 只能中断
事件循环上的 await（异步 LLM 请求），而工具执行 / HITL 忙等 / 委派子 Agent
跑在 asyncio.to_thread 的 worker 线程里——cancel 传播不到线程内部，
LLM 调用会继续跑完并计费（producer_task.cancel() 形同虚设）。

方案：每个请求绑定一个 threading.Event 取消标志，通过 contextvar 传播
（asyncio.to_thread 自动复制 contextvars，与 _stream_ctx 同一机制，
worker 线程和委派子 Agent 都读得到）。断连时 set()，四个检查点
（每次工具调用前 / HITL 轮询循环 / 委派子 Agent 启动前 / 异步执行循环顶部）
检测到即抛 RunCancelledError，执行链立即终止。

RunCancelledError 继承 BaseException 的原因：CrewAI 的 task retry 与
except Exception 兜底会吞掉普通异常（取消会被当成"工具报错"转成字符串
结果，Agent 换个姿势继续跑）；BaseException 只能被显式捕获，
保证取消语义穿透整个 Crew 执行栈。

另含活跃运行注册表：优雅停机用它实现"停止接新会话 → 等收尾窗口 →
强制取消"；/metrics 用它读并发数与队列深度。
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("run_control")


class RunCancelledError(BaseException):
    """运行被取消（客户端断连 / 服务停机强制终止）。

    继承 BaseException 以穿透 CrewAI 的 except Exception / task retry；
    调用方（chat producer）负责捕获并静默收尾，不给已断连的客户端发事件。
    """

    def __init__(self, reason: str = "cancelled"):
        super().__init__(reason)
        self.reason = reason


# 当前请求的取消标志（None = 本上下文未绑定运行，如后台任务/测试）
_cancel_event_ctx: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "run_cancel_event", default=None
)


def bind_run_cancel_event(event: threading.Event | None = None) -> contextvars.Token:
    """在当前上下文绑定取消标志（SSE 端点入口调用，创建 producer task 之前）。

    producer task 创建时会复制当前 context，其内部的 worker 线程
    （asyncio.to_thread）再复制一份——Event 是同一个对象，set() 对全链路可见。
    """
    return _cancel_event_ctx.set(event or threading.Event())


def unbind_run_cancel_event(token: contextvars.Token) -> None:
    _cancel_event_ctx.reset(token)


def current_cancel_event() -> threading.Event | None:
    return _cancel_event_ctx.get()


def cancel_current_run() -> bool:
    """取消当前上下文的运行（SSE 断连侧调用）。返回是否存在绑定的运行。"""
    evt = _cancel_event_ctx.get()
    if evt is None:
        return False
    evt.set()
    return True


def is_cancelled() -> bool:
    evt = _cancel_event_ctx.get()
    return evt is not None and evt.is_set()


def raise_if_cancelled() -> None:
    """取消检查点：worker 线程 / HITL 轮询 / 委派前的长跑路径定期调用。"""
    if is_cancelled():
        raise RunCancelledError()


# ── 活跃运行注册表（优雅停机 + /metrics 共用）─────────────────────


@dataclass
class RunEntry:
    run_id: str
    session: str
    event: threading.Event
    task: asyncio.Task | None = None
    queue: Any = None  # asyncio.Queue[AgentEvent | None]，供 /metrics 读深度
    created_at: float = field(default_factory=time.time)


_runs_lock = threading.Lock()
_active_runs: dict[str, RunEntry] = {}
_shutting_down = False


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:12]}"


def register_run(
    run_id: str,
    *,
    session: str = "",
    event: threading.Event | None = None,
    task: asyncio.Task | None = None,
    queue: Any = None,
) -> None:
    with _runs_lock:
        _active_runs[run_id] = RunEntry(
            run_id=run_id,
            session=session or run_id,
            event=event or threading.Event(),
            task=task,
            queue=queue,
        )


def unregister_run(run_id: str) -> None:
    with _runs_lock:
        _active_runs.pop(run_id, None)


def active_run_count() -> int:
    with _runs_lock:
        return len(_active_runs)


def begin_shutdown() -> None:
    """进入停机流程：chat 端点此后拒绝新会话（返回 503）。"""
    global _shutting_down
    _shutting_down = True
    logger.info("shutdown: 停止接受新会话，等待在跑 Crew 收尾")


def is_shutting_down() -> bool:
    return _shutting_down


def reset_for_tests() -> None:
    """测试隔离：清空注册表与停机标志。"""
    global _shutting_down
    with _runs_lock:
        _active_runs.clear()
    _shutting_down = False


async def wait_for_runs_to_finish(timeout: float) -> int:
    """等待在跑的运行结束，最多 timeout 秒。返回超时后仍未完成的数量。"""
    deadline = time.monotonic() + timeout
    while True:
        with _runs_lock:
            pending_tasks = [
                e.task for e in _active_runs.values()
                if e.task is not None and not e.task.done()
            ]
        if not pending_tasks:
            return 0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return len(pending_tasks)
        logger.info(
            "shutdown: %d 个运行进行中，剩余收尾窗口 %.0fs",
            len(pending_tasks), remaining,
        )
        await asyncio.wait(pending_tasks, timeout=min(remaining, 5.0))


def cancel_all_runs() -> int:
    """强制取消所有在跑运行（set 取消标志 + cancel task）。返回取消数量。"""
    with _runs_lock:
        entries = list(_active_runs.values())
    cancelled = 0
    for entry in entries:
        entry.event.set()
        if entry.task is not None and not entry.task.done():
            entry.task.cancel()
            cancelled += 1
    if cancelled:
        logger.warning("shutdown: 强制取消 %d 个未收尾的运行", cancelled)
    return cancelled


def snapshot_queue_depths() -> list[tuple[str, int]]:
    """各活跃运行的 SSE 队列当前深度（/metrics 用）。"""
    with _runs_lock:
        entries = list(_active_runs.values())
    out = []
    for e in entries:
        if e.queue is not None:
            try:
                out.append((e.session, e.queue.qsize()))
            except Exception:  # noqa: BLE001 - qsize 实现异常时跳过
                continue
    return out


def snapshot_sessions() -> list[str]:
    with _runs_lock:
        return [e.session for e in _active_runs.values()]

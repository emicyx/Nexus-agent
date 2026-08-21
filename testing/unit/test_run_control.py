"""A1 运行取消与活跃运行注册表单测（backend/app/core/run_control.py）。"""
import asyncio
import threading

import pytest

from app.core import run_control
from app.core.run_control import RunCancelledError


@pytest.fixture(autouse=True)
def _isolate():
    run_control.reset_for_tests()
    yield
    run_control.reset_for_tests()


def test_bind_cancel_raise_cycle():
    token = run_control.bind_run_cancel_event()
    try:
        # 未取消：检查点是 no-op
        run_control.raise_if_cancelled()
        assert not run_control.is_cancelled()

        # set 后：检查点抛 RunCancelledError（BaseException 子类）
        assert run_control.cancel_current_run()
        assert run_control.is_cancelled()
        with pytest.raises(RunCancelledError):
            run_control.raise_if_cancelled()
    finally:
        run_control.unbind_run_cancel_event(token)


def test_unbound_context_is_noop():
    # 未绑定运行的后台上下文：检查点不抛、取消返回 False
    run_control.raise_if_cancelled()
    assert not run_control.is_cancelled()
    assert not run_control.cancel_current_run()


async def test_cancel_flag_visible_across_to_thread():
    """A1 核心机制：断连侧 set() 的 Event 经 contextvar 复制对 worker 线程可见。"""
    token = run_control.bind_run_cancel_event()
    try:
        run_control.cancel_current_run()  # 模拟 SSE 断连侧
        seen_in_thread = await asyncio.to_thread(run_control.is_cancelled)
        assert seen_in_thread
        # worker 线程内检查点也应抛出
        with pytest.raises(BaseException) as exc_info:
            await asyncio.to_thread(run_control.raise_if_cancelled)
        assert isinstance(exc_info.value, RunCancelledError)
    finally:
        run_control.unbind_run_cancel_event(token)


def test_run_cancelled_error_is_base_exception():
    # 必须继承 BaseException：普通 Exception 会被 CrewAI 的 task retry /
    # except Exception 兜底吞掉，取消会被当成"工具报错"继续执行
    assert issubclass(RunCancelledError, BaseException)
    assert not issubclass(RunCancelledError, Exception)


def test_registry_lifecycle():
    evt = threading.Event()
    q = _FakeQueue(3)
    run_control.register_run("r1", session="s1", event=evt, queue=q)
    assert run_control.active_run_count() == 1
    assert run_control.snapshot_sessions() == ["s1"]
    assert run_control.snapshot_queue_depths() == [("s1", 3)]
    run_control.unregister_run("r1")
    assert run_control.active_run_count() == 0
    assert run_control.snapshot_queue_depths() == []


async def test_wait_then_force_cancel():
    cancelled = asyncio.Event()

    async def long_run():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(long_run())
    run_control.register_run("r9", session="s9", event=threading.Event(), task=task)

    # 收尾窗口内未完成 → 返回剩余数
    remaining = await run_control.wait_for_runs_to_finish(0.1)
    assert remaining == 1

    # 强制取消：set 取消标志 + cancel task
    assert run_control.cancel_all_runs() == 1
    await asyncio.sleep(0.05)
    assert cancelled.is_set()
    run_control.unregister_run("r9")


async def test_wait_returns_zero_when_all_done():
    async def quick():
        await asyncio.sleep(0.01)

    task = asyncio.create_task(quick())
    run_control.register_run("r2", session="s2", task=task)
    await task
    assert await run_control.wait_for_runs_to_finish(5) == 0
    run_control.unregister_run("r2")


def test_shutdown_flag():
    assert not run_control.is_shutting_down()
    run_control.begin_shutdown()
    assert run_control.is_shutting_down()


class _FakeQueue:
    def __init__(self, size):
        self._size = size

    def qsize(self):
        return self._size

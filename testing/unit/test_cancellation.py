"""A1 取消机制端到端回归：断连后 Crew 执行必须尽快终止，不再多花一次 LLM 调用。

场景与 chat.py 真实链路一致：
- 取消标志经 contextvar 绑定（producer/task/to_thread 全链路继承）
- 检查点1：包装过的工具调用前（tool_events.wrap_tool_with_events）
- 检查点4：异步执行循环顶部（crewai_async_patch）

判别性（全部确定性时序，不靠 sleep 猜）：
- 运行开始前已断连 → 一次 LLM 调用都不发（循环顶部检查点）
- 第一次 LLM 调用期间断连 → 工具一次都不执行（工具前检查点）
- 工具执行期间断连 → 当前工具跑完，但不再发起第二次 LLM 调用
"""
import asyncio
import threading
import time

import pytest
from crewai import Agent, Crew, Task
from crewai.tools import BaseTool

import app.crews.crewai_async_patch as async_patch  # noqa: F401  触发 monkey-patch
from app.core.run_control import (
    RunCancelledError,
    bind_run_cancel_event,
    cancel_current_run,
    unbind_run_cancel_event,
)
from app.crews.tool_events import wrap_tool_with_events
from app.llm.aliyun_llm import AliyunLLM

# 工具状态（BaseTool 是 pydantic 模型，类属性会被当字段，故用模块级字典）
_TOOL_STATE = {"started": threading.Event(), "runs": 0}


class SignalingTool(BaseTool):
    """标记开始 + 可控耗时，用于观察取消时机。"""

    name: str = "signaling_tool"
    description: str = "测试用工具"

    def _run(self, *args, **kwargs):
        _TOOL_STATE["runs"] += 1
        _TOOL_STATE["started"].set()
        time.sleep(0.3)
        return "tool done"


def _make_mock_llm(monkeypatch, first_call_gate: asyncio.Event | None = None):
    """有状态 Mock：第一次回 tool_call，之后回最终回答。

    first_call_gate：第一次 acall 先等该门再返回——制造"LLM 调用进行中"的
    确定性窗口（此时取消应被工具前检查点拦截）。
    """
    state = {"n": 0}

    def fake_call(self, messages, tools=None, callbacks=None, available_functions=None,
                  max_iterations=10, _retry_on_empty=True, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            return [{"function": {"name": "signaling_tool", "arguments": "{}"}}]
        return "最终回答"

    async def fake_acall(self, messages, tools=None, callbacks=None, available_functions=None,
                         max_iterations=10, _retry_on_empty=True, **kwargs):
        if state["n"] == 0 and first_call_gate is not None:
            await first_call_gate.wait()
        return fake_call(self, messages, tools=tools, callbacks=callbacks,
                         available_functions=available_functions,
                         max_iterations=max_iterations, _retry_on_empty=_retry_on_empty)

    monkeypatch.setattr(AliyunLLM, "call", fake_call)
    monkeypatch.setattr(AliyunLLM, "acall", fake_acall)
    return AliyunLLM(model="qwen-plus", api_key="sk-test"), state


def _build_crew(llm, tool):
    agent = Agent(
        role="测试角色",
        goal="测试目标",
        backstory="测试背景",
        llm=llm,
        memory=False,
        max_iter=5,
        tools=[tool],
    )
    task = Task(
        description="使用 signaling_tool",
        expected_output="一段回答",
        agent=agent,
    )
    return Crew(agents=[agent], tasks=[task], verbose=False)


def _reset_tool_state():
    _TOOL_STATE["started"].clear()
    _TOOL_STATE["runs"] = 0


async def _await_cancelled(kickoff) -> None:
    with pytest.raises(BaseException) as exc_info:
        await asyncio.wait_for(kickoff, timeout=8)
    assert isinstance(exc_info.value, RunCancelledError), (
        f"取消应穿透 akickoff 抛出 RunCancelledError，实际 {exc_info.value!r}"
    )


async def test_cancel_before_run_starts_zero_llm_calls(monkeypatch):
    """断连先于运行：循环顶部检查点拦截，零 LLM 调用、零工具执行。"""
    assert async_patch.is_applied()
    llm, state = _make_mock_llm(monkeypatch)
    crew = _build_crew(llm, SignalingTool())
    _reset_tool_state()

    token = bind_run_cancel_event()
    try:
        cancel_current_run()  # SSE 断连发生在 kickoff 之前
        kickoff = asyncio.create_task(crew.akickoff())
        await _await_cancelled(kickoff)
        assert state["n"] == 0, "取消后不应发起任何 LLM 调用"
        assert _TOOL_STATE["runs"] == 0
    finally:
        unbind_run_cancel_event(token)


async def test_cancel_during_first_llm_call_blocks_tool(monkeypatch):
    """断连发生在第一次 LLM 调用进行中：工具前检查点拦截，工具零执行。"""
    assert async_patch.is_applied()
    gate = asyncio.Event()
    llm, state = _make_mock_llm(monkeypatch, first_call_gate=gate)

    # 工具必须经过事件包装（检查点1 在包装器里）
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    tool = wrap_tool_with_events(
        SignalingTool(), queue, asyncio.get_running_loop(), agent_role="测试角色"
    )
    crew = _build_crew(llm, tool)
    _reset_tool_state()

    token = bind_run_cancel_event()
    try:
        kickoff = asyncio.create_task(crew.akickoff())
        await asyncio.sleep(0.15)  # 第一次 acall 已进入 gate 等待
        assert state["n"] == 0, "前提：第一次 LLM 调用尚未返回"
        cancel_current_run()
        gate.set()  # 放行第一次 LLM 返回 tool_call
        await _await_cancelled(kickoff)
        assert state["n"] == 1  # 第一次调用已发生（不可撤回）
        assert _TOOL_STATE["runs"] == 0, "工具前检查点应拦截工具执行"
    finally:
        unbind_run_cancel_event(token)


async def test_cancel_during_tool_stops_next_llm_call(monkeypatch):
    """断连发生在工具执行中：当前工具允许跑完，但不再发起第二次 LLM 调用。"""
    assert async_patch.is_applied()
    llm, state = _make_mock_llm(monkeypatch)
    crew = _build_crew(llm, SignalingTool())
    _reset_tool_state()

    token = bind_run_cancel_event()
    try:
        kickoff = asyncio.create_task(crew.akickoff())
        # 等工具真正开始（在 worker 线程里），再模拟断连
        started = await asyncio.to_thread(_TOOL_STATE["started"].wait, 5)
        assert started, "工具未在预期时间内启动"
        cancel_current_run()

        await _await_cancelled(kickoff)
        assert _TOOL_STATE["runs"] == 1  # 已开始的那次允许完成
        assert state["n"] == 1, "取消后不应有第二次 LLM 调用（省一次计费）"
    finally:
        unbind_run_cancel_event(token)

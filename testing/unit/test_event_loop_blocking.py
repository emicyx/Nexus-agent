"""回归测试：原生异步路径（Crew.akickoff）下工具执行不得阻塞事件循环。

背景：CrewAI 1.9.3 的 `Crew.akickoff()` 是真正的原生异步编排（全程 await，无
to_thread），agent 直接跑在事件循环上。其原生 function-calling 路径
`_ainvoke_loop_native_tools` 在 loop 内**同步内联**执行 `_handle_native_tool_calls`
→ `tool.run` → BlockingTool._run 的 time.sleep(2) 会冻结整个 loop。

改动后：crewai_async_patch 把 _handle_native_tool_calls offload 到 worker 线程
（asyncio.to_thread），loop 保持响应，heartbeat 持续跳动。

判别性：本测试用 crew.akickoff()（非 kickoff_async，后者是 to_thread 线程桥接，
工具本就不在 loop 上）。patch 生效 → heartbeat>50（PASS）；patch 失效 → loop 被
time.sleep 冻结 → heartbeat≈0（FAIL）。

前提：必须先 import app.crews.crewai_async_patch 触发 monkey-patch（幂等）。
"""
import asyncio
import time

import pytest
from crewai import Agent, Crew, Task
from crewai.tools import BaseTool

import app.crews.crewai_async_patch as async_patch  # noqa: F401  触发 monkey-patch
from app.llm.aliyun_llm import AliyunLLM


class BlockingTool(BaseTool):
    """阻塞 2 秒的工具，验证事件循环不被冻结。"""

    name: str = "blocking_tool"
    description: str = "阻塞 2 秒的工具，验证事件循环不被冻结"

    def _run(self, *args, **kwargs):
        time.sleep(2.0)
        return "tool done"


def _make_mock_llm(monkeypatch):
    """有状态 Mock：第一次回 tool_call，第二次回最终回答。"""
    state = {"n": 0}

    def fake_call(self, messages, tools=None, callbacks=None, available_functions=None,
                  max_iterations=10, _retry_on_empty=True, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            return [{"function": {"name": "blocking_tool", "arguments": "{}"}}]
        return "最终回答"

    async def fake_acall(self, messages, tools=None, callbacks=None, available_functions=None,
                         max_iterations=10, _retry_on_empty=True, **kwargs):
        return fake_call(self, messages, tools=tools, callbacks=callbacks,
                         available_functions=available_functions,
                         max_iterations=max_iterations, _retry_on_empty=_retry_on_empty)

    monkeypatch.setattr(AliyunLLM, "call", fake_call)
    monkeypatch.setattr(AliyunLLM, "acall", fake_acall)
    return AliyunLLM(model="qwen-plus", api_key="sk-test")


async def test_event_loop_not_blocked_during_blocking_tool(monkeypatch):
    assert async_patch.is_applied(), "native-tool async patch 未生效，回归测试前提不成立"
    llm = _make_mock_llm(monkeypatch)

    agent = Agent(
        role="测试角色",
        goal="测试目标",
        backstory="测试背景",
        llm=llm,
        memory=False,
        max_iter=5,
        tools=[BlockingTool()],
    )
    task = Task(
        description="使用 blocking_tool",
        expected_output="一段回答",
        agent=agent,
    )
    crew = Crew(agents=[agent], tasks=[task], verbose=False)

    stop = asyncio.Event()
    ticks = {"n": 0}

    async def heartbeat():
        while not stop.is_set():
            await asyncio.sleep(0.01)
            ticks["n"] += 1

    hb = asyncio.create_task(heartbeat())
    t0 = time.monotonic()
    try:
        # 用 akickoff()（原生异步编排，agent 跑在事件循环上）而非 kickoff_async()
        # （后者 = asyncio.to_thread(kickoff)，工具本就不在 loop 上，无法判别）
        await crew.akickoff()
    finally:
        stop.set()
        await hb
    elapsed = time.monotonic() - t0

    # 工具 sleep 2s 期间：loop 被冻结则 heartbeat 几乎不推进（≈0 次）；
    # 正常 offload 下 2s/0.01s ≈ 200 次。宽松下界 50 防 CI 抖动。
    assert ticks["n"] > 50, (
        f"事件循环疑似被工具阻塞（heartbeat 仅 {ticks['n']} 次，耗时 {elapsed:.2f}s）"
    )

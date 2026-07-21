"""工具事件包装器 - 为 CrewAI BaseTool 注入 tool_call / tool_result 事件推送

Week 2: 让前端能实时看到"正在检索..."、"工具调用完成"等状态。
通过包装 BaseTool._run 方法，在工具执行前后向 asyncio.Queue 推送事件。

用法：
    tool = wrap_tool_with_events(tool, queue, loop, agent_role="研究员")
"""
import asyncio
import logging
from typing import Any

from crewai.tools import BaseTool

from app.core.events import AgentEvent

logger = logging.getLogger("tools")


def _truncate(text: Any, limit: int = 500) -> str:
    """截断过长的工具输入/输出，避免 SSE 消息过大。"""
    s = text if isinstance(text, str) else str(text)
    return s if len(s) <= limit else s[:limit] + "...(truncated)"


def _safe_put(queue: "asyncio.Queue[AgentEvent | None]", evt: AgentEvent, loop: asyncio.AbstractEventLoop) -> None:
    """线程安全的 Queue 推送。

    Crew 执行在 thread pool 中运行，不在事件循环线程，必须用
    call_soon_threadsafe 投递到事件循环线程 push，避免数据竞争。
    """
    loop.call_soon_threadsafe(queue.put_nowait, evt)


def wrap_tool_with_events(
    tool: BaseTool,
    queue: "asyncio.Queue[AgentEvent | None]",
    loop: asyncio.AbstractEventLoop,
    agent_role: str = "Agent",
) -> BaseTool:
    """包装一个 CrewAI 工具，使其在执行前后推送 tool_call / tool_result 事件。

    通过 monkey-patch tool._run 实现，保留原逻辑不变。
    """
    original_run = tool._run
    tool_name = tool.name

    def wrapped_run(*args: Any, **kwargs: Any) -> str:
        # 推送 tool_call 开始事件
        call_input = _truncate(kwargs if kwargs else (args[0] if args else ""))
        _safe_put(
            queue,
            AgentEvent(
                type="tool_call",
                agent=agent_role,
                tool=tool_name,
                input=call_input,
            ),
            loop,
        )

        try:
            result = original_run(*args, **kwargs)
        except Exception as e:
            # 工具异常也作为结果推送，便于前端展示
            _safe_put(
                queue,
                AgentEvent(
                    type="tool_result",
                    agent=agent_role,
                    tool=tool_name,
                    output=f"工具执行出错: {e}",
                ),
                loop,
            )
            raise

        # 推送 tool_result 完成事件
        _safe_put(
            queue,
            AgentEvent(
                type="tool_result",
                agent=agent_role,
                tool=tool_name,
                output=_truncate(result),
            ),
            loop,
        )
        return result

    tool._run = wrapped_run  # type: ignore[method-assign]
    return tool

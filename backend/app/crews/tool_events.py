"""工具事件包装器 - 为 CrewAI BaseTool 注入 tool_call / tool_result 事件推送

Week 2: 让前端能实时看到"正在检索..."、"工具调用完成"等状态。
通过包装 BaseTool._run 方法，在工具执行前后向 asyncio.Queue 推送事件。

用法：
    tool = wrap_tool_with_events(tool, queue, loop, agent_role="研究员")
"""
import asyncio
import logging
import time
from typing import Any

from crewai.tools import BaseTool

from app.core.events import AgentEvent, try_put
from app.core.metrics import record_tool_call
from app.core.run_control import RunCancelledError, raise_if_cancelled
from app.core.token_budget import check_budget_checkpoint

logger = logging.getLogger("tools")


def _truncate(text: Any, limit: int = 500) -> str:
    """截断过长的工具输入/输出，避免 SSE 消息过大。"""
    s = text if isinstance(text, str) else str(text)
    return s if len(s) <= limit else s[:limit] + "...(truncated)"


def _safe_put(queue: "asyncio.Queue[AgentEvent | None]", evt: AgentEvent, loop: asyncio.AbstractEventLoop) -> None:
    """线程安全的 Queue 推送。

    Crew 执行在 thread pool 中运行，不在事件循环线程，必须用
    call_soon_threadsafe 投递到事件循环线程 push，避免数据竞争。
    队列有界（A3）：满时丢最旧事件，防止慢客户端拖爆内存。
    """
    loop.call_soon_threadsafe(try_put, queue, evt)


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
        # A1 取消检查点：客户端断连后不再启动新的工具调用（RunCancelledError
        # 是 BaseException，不会被下方 except Exception 转成"工具报错"结果）
        raise_if_cancelled()
        # A2 预算检查点（30s 节流）：长跑 Crew 中途超预算时终止后续工具调用。
        # BudgetExceededRunError 是 BaseException（同取消语义），穿透 CrewAI
        # 工具异常兜底直达 producer，由其分类为 budget_exceeded 事件。
        check_budget_checkpoint()

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

        _t0 = time.perf_counter()
        try:
            result = original_run(*args, **kwargs)
        except RunCancelledError:
            raise  # 取消向上传播，不计为工具失败
        except Exception as e:
            record_tool_call(tool_name, time.perf_counter() - _t0, ok=False)
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

        record_tool_call(tool_name, time.perf_counter() - _t0, ok=True)

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

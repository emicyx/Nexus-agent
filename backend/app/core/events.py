"""事件总线 + SSE 格式化

SSE 事件协议：
    event: <type>
    data: <json>

事件类型：
    agent_thinking      - Agent 思考步骤（content=思考文本, agent=Agent角色, step=序号）
    tool_call           - 工具调用开始（agent, tool, input）
    tool_result         - 工具调用结束（agent, tool, output）
    approval_requested  - HITL 审批请求（agent, tool, input含approval_id/action/risk_level）
    token               - 最终回答分块
    final_answer        - 最终完整回答
    error               - 错误
    done                - 流结束哨兵
"""
import asyncio
import json
import time
from dataclasses import dataclass, asdict, field
from typing import Any, AsyncIterator


@dataclass
class AgentEvent:
    """Agent 执行过程中的事件"""
    type: str
    content: str = ""
    step: int | None = None
    agent: str | None = None       # Agent 角色名，用于区分不同 Agent
    tool: str | None = None        # 工具名（tool_call / tool_result 事件）
    input: Any | None = None       # 工具输入（tool_call 事件）
    output: Any | None = None      # 工具输出（tool_result 事件）
    ts: float = field(default_factory=time.time)


def format_sse(event: AgentEvent) -> str:
    """将 AgentEvent 格式化为 SSE 字符串。"""
    payload = {k: v for k, v in asdict(event).items() if v is not None}
    return f"event: {event.type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def event_stream(
    queue: "asyncio.Queue[AgentEvent | None]",
    keepalive_interval: int = 15,
) -> AsyncIterator[str]:
    """从 asyncio.Queue 消费事件，输出 SSE 字符串流。

    None 是哨兵值，表示流结束。
    """
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=keepalive_interval)
        except asyncio.TimeoutError:
            yield ": ping\n\n"
            continue
        if item is None:
            yield format_sse(AgentEvent(type="done"))
            return
        yield format_sse(item)

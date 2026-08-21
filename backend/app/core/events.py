"""事件总线 + SSE 格式化

SSE 事件协议：
    event: <type>
    data: <json>

事件类型：
    agent_thinking      - Agent 思考步骤（content=思考文本, agent=Agent角色, step=序号）
    thinking_token      - Agent 思考 streaming token（content=单个/少量 token, agent=角色, step=序号）
    tool_call           - 工具调用开始（agent, tool, input）
    tool_result         - 工具调用结束（agent, tool, output）
    approval_requested  - HITL 审批请求（agent, tool, input含approval_id/action/risk_level）
    token               - 最终回答分块
    final_answer        - 最终完整回答
    task_completed      - 任务级产出（agent, output含task_name/output_format/pydantic_valid/raw_preview）
    delegation          - manager 委派（agent, input含task/context/coworker）
    error               - 错误（content=用户友好提示, error_kind=分类见 core/llm_errors.py）
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
    error_kind: str | None = None  # error 事件分类（token_limit/rate_limit/auth/timeout/network/server/budget_exceeded/unknown）
    ts: float = field(default_factory=time.time)


def format_sse(event: AgentEvent) -> str:
    """将 AgentEvent 格式化为 SSE 字符串。"""
    payload = {k: v for k, v in asdict(event).items() if v is not None}
    return f"event: {event.type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


# 队列满时被丢弃的事件计数（/metrics 暴露，观察慢客户端压力）
_dropped_event_count = 0


def dropped_event_count() -> int:
    return _dropped_event_count


def try_put(
    queue: "asyncio.Queue[AgentEvent | None]",
    event: "AgentEvent | None",
) -> bool:
    """非阻塞投递；队列满时丢弃最旧事件后重试（A3 慢客户端背压保护）。

    普通事件（工具/思考/token，丢了只影响实时性）与断连兜底的 None 哨兵走这里；
    final_answer / error / 正常结束的哨兵由 producer 用 `await queue.put()` 投递，
    自带背压，不受丢弃策略影响。
    """
    global _dropped_event_count
    try:
        queue.put_nowait(event)
        return True
    except asyncio.QueueFull:
        try:
            queue.get_nowait()  # 丢最旧
        except asyncio.QueueEmpty:
            return False
        _dropped_event_count += 1
        try:
            queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            return False


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

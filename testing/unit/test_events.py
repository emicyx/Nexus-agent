"""SSE 事件协议单测（backend/app/core/events.py）。"""
import asyncio
import json

import pytest

from app.core.events import AgentEvent, dropped_event_count, event_stream, format_sse, try_put


def test_format_sse_basic():
    evt = AgentEvent(type="agent_thinking", content="思考中", step=1, agent="研究员")
    s = format_sse(evt)
    assert s.startswith("event: agent_thinking\n")
    # 第二行是 data: <json>
    _, data_line = s.split("\n", 1)
    assert data_line.startswith("data: ")
    # 以空行结尾（SSE 分隔）
    assert s.endswith("\n\n")
    # JSON 可解析且字段正确
    payload = json.loads(data_line[len("data: ") :].strip())
    assert payload["type"] == "agent_thinking"
    assert payload["content"] == "思考中"
    assert payload["agent"] == "研究员"
    assert payload["step"] == 1


def test_format_sse_omits_none_fields():
    evt = AgentEvent(type="done")
    s = format_sse(evt)
    _, data_line = s.split("\n", 1)
    payload = json.loads(data_line[len("data: ") :].strip())
    # None 默认字段（step/agent/tool/input/output）被省略
    assert "step" not in payload
    assert "agent" not in payload
    assert "tool" not in payload
    assert "input" not in payload
    assert "output" not in payload
    assert payload["type"] == "done"


def test_format_sse_preserves_chinese():
    evt = AgentEvent(type="token", content="你好，世界")
    s = format_sse(evt)
    assert "你好，世界" in s  # ensure_ascii=False，中文不被转义


async def test_event_stream_orders_and_done():
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(AgentEvent(type="agent_thinking", content="a"))
    queue.put_nowait(AgentEvent(type="token", content="b"))
    queue.put_nowait(None)  # 哨兵

    items = [x async for x in event_stream(queue)]
    # 最后一个必须是 done 事件
    assert len(items) == 3
    assert items[0].startswith("event: agent_thinking\n")
    assert items[1].startswith("event: token\n")
    assert items[2].startswith("event: done\n")


async def test_event_stream_keepalive_ping():
    """超时未收到事件时应产出 ': ping' 注释行（keep-alive）。"""
    queue: asyncio.Queue = asyncio.Queue()

    async def push_later():
        await asyncio.sleep(0.1)
        queue.put_nowait(None)

    task = asyncio.create_task(push_later())
    items = [x async for x in event_stream(queue, keepalive_interval=0.05)]
    await task
    # 至少产出一个 ping，最后是 done
    assert any(i == ": ping\n\n" for i in items)
    assert items[-1].startswith("event: done\n")


# ---------- A3：有界队列安全投递 ----------


def test_try_put_puts_when_space():
    q: asyncio.Queue = asyncio.Queue(maxsize=2)
    assert try_put(q, AgentEvent(type="token", content="1")) is True
    assert try_put(q, AgentEvent(type="token", content="2")) is True
    assert q.qsize() == 2


def test_try_put_drops_oldest_when_full():
    q: asyncio.Queue = asyncio.Queue(maxsize=2)
    before = dropped_event_count()
    try_put(q, AgentEvent(type="token", content="1"))
    try_put(q, AgentEvent(type="token", content="2"))
    # 满后再投：丢最旧（"1"），新事件入队
    assert try_put(q, AgentEvent(type="token", content="3")) is True
    items = [q.get_nowait(), q.get_nowait()]
    contents = [i.content for i in items]
    assert contents == ["2", "3"]
    assert dropped_event_count() == before + 1


def test_try_put_sentinel_on_full():
    """断连兜底场景：哨兵 None 也走丢最旧策略强塞（消费端已不在时防悬挂）。"""
    q: asyncio.Queue = asyncio.Queue(maxsize=1)
    try_put(q, AgentEvent(type="token", content="1"))
    assert try_put(q, None) is True
    assert q.get_nowait() is None

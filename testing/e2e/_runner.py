"""E2E 辅助：SSE 流式聊天 + 事件记录。"""
import json
import time
from pathlib import Path


def _parse_block(block: str) -> dict:
    evt_type = None
    data = {}
    for line in block.strip().split("\n"):
        if line.startswith("event: "):
            evt_type = line[len("event: "):].strip()
        elif line.startswith("data: "):
            data = json.loads(line[len("data: "):].strip())
    return {"type": evt_type, "data": data}


async def stream_chat(client, payload, record_path=None, timeout=180):
    """POST /v1/chat/stream，收集事件 + 计时。返回 (events, elapsed_sec)。"""
    events = []
    t0 = time.perf_counter()
    async with client.stream("POST", "/v1/chat/stream", json=payload) as resp:
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        block = ""
        async for line in resp.aiter_lines():
            if line == "":
                if block.strip():
                    events.append(_parse_block(block))
                block = ""
            else:
                block += line + "\n"
    elapsed = time.perf_counter() - t0
    if record_path:
        Path(record_path).write_text(
            json.dumps(events, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return events, elapsed


def event_types(events) -> list[str]:
    return [e["type"] for e in events]


def find_event(events, evt_type) -> dict | None:
    return next((e for e in events if e["type"] == evt_type), None)


def find_tool_call(events, tool) -> dict | None:
    return next(
        (e for e in events if e["type"] == "tool_call" and e["data"].get("tool") == tool),
        None,
    )

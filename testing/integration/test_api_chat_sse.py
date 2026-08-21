"""SSE 聊天管道集成测试（Mock LLM，验证事件序列与格式）。

覆盖：无状态工厂从 DB 装配 Crew → kickoff → asyncio.Queue → SSE 流。
这是本项目最核心的协议，必须验证逐行格式 + 事件顺序。
"""
import json

from fastapi.testclient import TestClient

from app.config import settings
from testing.integration._helpers import cleanup_agent, cleanup_crew, create_agent, create_crew, uniq

MOCK_ANSWER = "这是 Mock LLM 的固定回答。"


def _parse_sse(body: str) -> list[dict]:
    """把 SSE body 解析为 [{type, data}]，同时校验逐行格式。"""
    events = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        evt_type = None
        data = {}
        for line in lines:
            assert line.startswith("event: ") or line.startswith("data: "), f"非法 SSE 行: {line!r}"
            if line.startswith("event: "):
                evt_type = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        assert evt_type is not None
        events.append({"type": evt_type, "data": data})
    return events


def _setup_minimal_crew(client):
    """创建一个最小 sequential crew（单 agent 单 task，无工具，memory=False）。"""
    agent = create_agent(client)  # memory=False
    aid = agent["id"]
    task = {
        "name": uniq("task"),
        "description": "回答用户问题：{user_input}",
        "expected_output": "一段中文回答",
        "agent_id": aid,
        "position": 0,
    }
    crew = create_crew(client, agent_ids=[aid], tasks=[task])
    return aid, crew["id"]


def test_chat_stream_full_sequence(client: TestClient, monkeypatch):
    aid, cid = _setup_minimal_crew(client)
    # 禁掉 LTM/KB 预注入（需要真实 embedding），只走 STM + Mock LLM
    monkeypatch.setattr(settings, "LTM_USER_MEMORY_ENABLED", False)
    monkeypatch.setattr(settings, "KB_PREINJECT_ENABLED", False)
    session_uuid = "test-chat-" + uniq("s")
    try:
        payload = {
            "message": "请介绍一下你自己",
            "session_id": session_uuid,
            "crew_id": cid,
            "single": False,
        }
        with client.stream("POST", "/v1/chat/stream", json=payload) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            body = "".join(resp.iter_text())
        assert body.strip(), "SSE body 为空"
        events = _parse_sse(body)
        types = [e["type"] for e in events]
        # 关键事件必须出现，且 done 在最后
        assert "agent_thinking" in types, f"缺 agent_thinking: {types}"
        assert "final_answer" in types, f"缺 final_answer: {types}"
        assert types[-1] == "done", f"最后一条必须是 done: {types}"
        # 首个事件是 Crew 启动思考
        assert events[0]["type"] == "agent_thinking"
        assert events[0]["data"]["agent"] == "Crew"
        # final_answer 内容 = Mock 回答
        fa = next(e for e in events if e["type"] == "final_answer")
        assert fa["data"]["content"] == MOCK_ANSWER, f"final_answer 内容不符: {fa['data']['content']!r}"
        # 消息已持久化：session 详情里应有 user + assistant 两条
        r = client.get("/v1/chat/sessions", params={"crew_id": cid})
        sess = next(s for s in r.json() if s["session_uuid"] == session_uuid)
        detail = client.get(f"/v1/chat/sessions/{sess['id']}").json()
        roles = [m["role"] for m in detail["messages"]]
        assert roles == ["user", "assistant"], f"消息未持久化: {roles}"
        # 清理 session
        client.delete(f"/v1/chat/sessions/{sess['id']}")
    finally:
        cleanup_crew(client, cid)
        cleanup_agent(client, aid)


def test_chat_single_mode(client: TestClient, monkeypatch):
    """single=true 走无 DB 回退单 Agent，也应产出完整 SSE 闭环。"""
    monkeypatch.setattr(settings, "LTM_USER_MEMORY_ENABLED", False)
    monkeypatch.setattr(settings, "KB_PREINJECT_ENABLED", False)
    with client.stream("POST", "/v1/chat/stream", json={
        "message": "1+1等于几",
        "single": True,
        "crew_id": None,
        "session_id": None,
    }) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    events = _parse_sse(body)
    types = [e["type"] for e in events]
    assert "final_answer" in types
    assert types[-1] == "done"
    fa = next(e for e in events if e["type"] == "final_answer")
    assert fa["data"]["content"] == MOCK_ANSWER

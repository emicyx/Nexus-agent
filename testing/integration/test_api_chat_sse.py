"""SSE 聊天管道集成测试（Mock LLM，验证事件序列与格式）。

覆盖：无状态工厂从 DB 装配 Crew → kickoff → asyncio.Queue → SSE 流。
这是本项目最核心的协议，必须验证逐行格式 + 事件顺序。
"""
import json

from fastapi.testclient import TestClient

from app.config import settings
from testing.integration._helpers import cleanup_agent, cleanup_crew, create_agent, create_crew, uniq

MOCK_ANSWER = "这是 Mock LLM 的固定回答。"

# 挂 ResearchMaterial output_schema 的 crew（researcher_writer）需要合法 JSON 输出
SCHEMA_MOCK_ANSWER = '{"title": "t", "key_facts": ["f"], "sources": [], "summary": "s"}'


def _async_return(value: str):
    async def _fake(self, *args, **kwargs):
        return value
    return _fake


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


# ==================== v2 R0：Auto 模式（路由 v0） ====================

def test_chat_auto_mode_default_route(client: TestClient, monkeypatch):
    """mode=auto 无命令前缀 → 默认 crew（researcher_writer），首事件 routed_crew。"""
    monkeypatch.setattr(settings, "LTM_USER_MEMORY_ENABLED", False)
    monkeypatch.setattr(settings, "KB_PREINJECT_ENABLED", False)
    # researcher_writer 的 research 任务挂 ResearchMaterial output_schema，
    # 默认 Mock 的固定中文串过不了 pydantic JSON 解析，本用例改回合法 JSON
    from app.llm.aliyun_llm import AliyunLLM

    schema_answer = SCHEMA_MOCK_ANSWER
    monkeypatch.setattr(AliyunLLM, "call", lambda self, *a, **k: schema_answer)
    monkeypatch.setattr(
        AliyunLLM, "acall",
        _async_return(schema_answer),
    )
    with client.stream("POST", "/v1/chat/stream", json={
        "message": "请介绍一下你自己",
        "mode": "auto",
        "crew_id": None,
        "session_id": None,
        "single": False,
    }) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    events = _parse_sse(body)
    types = [e["type"] for e in events]
    # 首事件是路由决策（透明度契约），随后正常执行
    assert types[0] == "routed_crew", f"首事件应为 routed_crew: {types[:5]}"
    assert events[0]["data"]["content"] == "researcher_writer"
    assert events[0]["data"]["input"]["command"] is None
    assert "final_answer" in types
    assert types[-1] == "done"


def test_chat_auto_mode_command_route(client: TestClient, monkeypatch):
    """mode=auto /kb 命令 → knowledge_qa，命令前缀剥离后送入 crew 并持久化。"""
    monkeypatch.setattr(settings, "LTM_USER_MEMORY_ENABLED", False)
    monkeypatch.setattr(settings, "KB_PREINJECT_ENABLED", False)
    r = client.get("/v1/crews")
    kb_crew = next(c for c in r.json() if c["name"] == "knowledge_qa")
    session_uuid = "test-auto-" + uniq("s")
    with client.stream("POST", "/v1/chat/stream", json={
        "message": "/kb 混合检索怎么配置",
        "mode": "auto",
        "crew_id": None,
        "session_id": session_uuid,
        "single": False,
    }) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    events = _parse_sse(body)
    types = [e["type"] for e in events]
    assert types[0] == "routed_crew"
    assert events[0]["data"]["content"] == "knowledge_qa"
    assert events[0]["data"]["input"]["command"] == "/kb"
    assert "final_answer" in types
    assert types[-1] == "done"
    # 会话绑定到路由命中的 crew；持久化的用户消息已剥离命令前缀
    r2 = client.get("/v1/chat/sessions", params={"crew_id": kb_crew["id"]})
    sess = next(s for s in r2.json() if s["session_uuid"] == session_uuid)
    detail = client.get(f"/v1/chat/sessions/{sess['id']}").json()
    assert detail["messages"][0]["content"] == "混合检索怎么配置"
    client.delete(f"/v1/chat/sessions/{sess['id']}")


def test_chat_auto_mode_unknown_command_400(client: TestClient):
    """未知命令 → 400 + 可用命令清单（不进 LLM）。"""
    r = client.post("/v1/chat/stream", json={
        "message": "/nosuch 帮我做事",
        "mode": "auto",
    })
    assert r.status_code == 400
    assert "未知命令 /nosuch" in r.text
    assert "/kb" in r.text and "/write" in r.text


def test_chat_auto_mode_conflict_400(client: TestClient):
    """mode=auto 与 crew_id 互斥 → 400。"""
    r = client.post("/v1/chat/stream", json={
        "message": "你好",
        "mode": "auto",
        "crew_id": 1,
    })
    assert r.status_code == 400
    assert "互斥" in r.text


def test_retired_crews_absent_from_catalog(client: TestClient):
    """v2 R0：退役 crew 被 seed 同步从存量 DB 删除，保留 crew 齐全。"""
    r = client.get("/v1/crews")
    names = {c["name"] for c in r.json()}
    assert "team_orchestrator" not in names
    assert "safety_check" not in names
    for required in ("researcher_writer", "knowledge_qa", "web_ingest_crew", "iterative_write_crew"):
        assert required in names, f"保留 crew 缺失: {required}"

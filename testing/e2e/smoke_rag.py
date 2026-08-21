"""E2E：Agentic RAG —— 上传文档后 Agent 自主调 rag_search 并给出有依据回答。"""
import pytest

from testing.e2e._runner import event_types, find_event, find_tool_call, stream_chat

DOC_CONTENT = (
    "公司请假制度：\n\n员工年假为 5 天，工作满三年后增至 10 天。\n\n"
    "请假需提前一天申请，需直属主管审批。"
)


@pytest.mark.e2e
async def test_rag_grounded_answer(client, evidence):
    # 1. 上传文档（真实 embedding）
    r = await client.post("/v1/documents", json={"name": "e2e_rag_doc", "content": DOC_CONTENT})
    assert r.status_code == 201, r.text
    doc_id = r.json()["id"]
    try:
        # 2. 找 knowledge_qa crew（纯 RAG 单 Agent）
        r = await client.get("/v1/crews")
        crews = r.json()
        crew = next(c for c in crews if c["name"] == "knowledge_qa")
        # 3. 提问，答案只在文档里
        events, elapsed = await stream_chat(
            client,
            {"message": "根据知识库，公司年假有几天？工作满三年后呢？",
             "crew_id": crew["id"], "session_id": None, "single": False},
            record_path=evidence / "smoke_rag.json",
        )
        types = event_types(events)
        assert "final_answer" in types, f"缺 final_answer: {types}"
        fa = find_event(events, "final_answer")
        content = fa["data"]["content"]
        # 4. Agent 自主调 rag_search（核心卖点）
        rag_call = find_tool_call(events, "rag_search")
        assert rag_call is not None, f"Agent 未调用 rag_search，事件类型: {types}"
        # 5. 回答有依据（命中文档关键数字）
        assert "5" in content, f"回答未命中文档内容: {content}"
        print(f"\n[smoke] RAG 耗时 {elapsed:.1f}s，rag_search 已调用，回答含 '5' ✅")
    finally:
        await client.delete(f"/v1/documents/{doc_id}")

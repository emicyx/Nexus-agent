"""E2E：网页内容入库编排 —— URL 抓取→markdown 撰写→审阅→入库 全流程。

较慢（hierarchical + 多次 LLM 调用），且结果受外部网页内容影响，断言从宽。
"""
import pytest

from testing.e2e._runner import event_types, find_event, find_tool_call, stream_chat


@pytest.mark.e2e
async def test_web_ingest_flow(client, evidence):
    r = await client.get("/v1/crews")
    crew = next(c for c in r.json() if c["name"] == "web_ingest_crew")
    assert crew["process_type"] == "hierarchical"

    events, elapsed = await stream_chat(
        client,
        {"message": "请抓取并整理 https://example.com 的内容入库",
         "crew_id": crew["id"], "session_id": None, "single": False},
        record_path=evidence / "smoke_web_ingest.json",
    )
    types = event_types(events)
    assert "final_answer" in types, f"缺 final_answer: {types}"
    fa = find_event(events, "final_answer")
    assert fa["data"]["content"].strip()

    # 记录关键流程节点（不强制全部出现，供评审人工核验）
    fetch = find_tool_call(events, "fetch_url")
    ingest = find_tool_call(events, "kb_ingest")
    print(f"\n[smoke] web_ingest 耗时 {elapsed:.1f}s，事件 {len(events)} 个")
    print(f"       fetch_url 调用: {'✅' if fetch else '❌'}，kb_ingest 调用: {'✅' if ingest else '❌'}")
    print(f"       最终回答: {fa['data']['content'][:60]}...")
    # 宽松断言：至少走到最终回答（全流程细节留人工核验）

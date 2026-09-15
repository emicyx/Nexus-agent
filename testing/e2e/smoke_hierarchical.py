"""E2E：hierarchical 编排 —— manager agent 拆解并作答。

v2 R0 改挂 iterative_write_crew（team_orchestrator 已退役）：同样是
hierarchical + manager_agent 形态，manager 为「循环编排主管」。
"""
import pytest

from testing.e2e._runner import event_types, find_event, stream_chat


@pytest.mark.e2e
async def test_hierarchical_manager_loop(client, evidence):
    r = await client.get("/v1/crews")
    crew = next(c for c in r.json() if c["name"] == "iterative_write_crew")
    assert crew["process_type"] == "hierarchical"

    events, elapsed = await stream_chat(
        client,
        {"message": "写一段 30 字以内的短句介绍混合检索，保存成便签", "crew_id": crew["id"], "session_id": None, "single": False},
        record_path=evidence / "smoke_hierarchical.json",
    )
    types = event_types(events)
    # manager 的思考以「循环编排主管」身份出现
    managers = [e for e in events if e["type"] == "agent_thinking" and e["data"].get("agent") == "循环编排主管"]
    assert managers, f"未出现 manager('循环编排主管') 思考: {types}"
    assert "final_answer" in types, f"缺 final_answer: {types}"
    fa = find_event(events, "final_answer")
    assert fa["data"]["content"].strip()
    print(f"\n[smoke] hierarchical 耗时 {elapsed:.1f}s，manager 决策 {len(managers)} 次 ✅")

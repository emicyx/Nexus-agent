"""E2E：hierarchical 编排 —— manager agent 拆解并作答。"""
import pytest

from testing.e2e._runner import event_types, find_event, stream_chat


@pytest.mark.e2e
async def test_hierarchical_manager_loop(client, evidence):
    r = await client.get("/v1/crews")
    crew = next(c for c in r.json() if c["name"] == "team_orchestrator")
    assert crew["process_type"] == "hierarchical"

    events, elapsed = await stream_chat(
        client,
        {"message": "1+1等于几", "crew_id": crew["id"], "session_id": None, "single": False},
        record_path=evidence / "smoke_hierarchical.json",
    )
    types = event_types(events)
    # manager 的思考以「团队主管」身份出现
    managers = [e for e in events if e["type"] == "agent_thinking" and e["data"].get("agent") == "团队主管"]
    assert managers, f"未出现 manager('团队主管') 思考: {types}"
    assert "final_answer" in types, f"缺 final_answer: {types}"
    fa = find_event(events, "final_answer")
    assert fa["data"]["content"].strip()
    print(f"\n[smoke] hierarchical 耗时 {elapsed:.1f}s，manager 决策 {len(managers)} 次 ✅")

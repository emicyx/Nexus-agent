"""E2E：默认 researcher_writer crew 真实对话，验证 SSE 完整闭环。"""
import pytest

from testing.e2e._runner import event_types, find_event, stream_chat


@pytest.mark.e2e
async def test_single_chat_full_loop(client, evidence):
    events, elapsed = await stream_chat(
        client,
        {"message": "1+1等于几", "crew_id": None, "session_id": None, "single": False},
        record_path=evidence / "smoke_single_chat.json",
    )
    types = event_types(events)
    assert "agent_thinking" in types, f"缺 agent_thinking: {types}"
    assert "final_answer" in types, f"缺 final_answer: {types}"
    assert types[-1] == "done", f"最后一条非 done: {types}"
    fa = find_event(events, "final_answer")
    assert fa["data"]["content"].strip(), "final_answer 为空"
    print(f"\n[smoke] 耗时 {elapsed:.1f}s，事件数 {len(events)}，最终回答: {fa['data']['content'][:60]}...")

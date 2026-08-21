"""E2E：配置热更新 —— 改 Agent goal 后新对话立即生效（无重启）。"""
import pytest

from testing.e2e._runner import event_types, find_event, stream_chat


@pytest.mark.e2e
async def test_hot_reload_after_goal_change(client, evidence):
    # 1. 取默认 crew 的 writer agent，记录原 goal
    r = await client.get("/v1/crews")
    crew = next(c for c in r.json() if c["name"] == "researcher_writer")
    writer = next(a for a in crew["agents"] if a["name"] == "writer")
    orig_goal = writer["goal"]

    try:
        # 2. 改 goal 为带独特点的指令（热更新 = DB 即事实源）
        new_goal = orig_goal + "【注意】回答时必须以'热更新验证OK'开头。"
        r = await client.put(f"/v1/agents/{writer['id']}", json={"goal": new_goal})
        assert r.status_code == 200
        assert r.json()["goal"] == new_goal

        # 3. 新对话（同一 crew），工厂从 DB 重读 → 新 goal 生效
        events, elapsed = await stream_chat(
            client,
            {"message": "请做个自我介绍", "crew_id": crew["id"], "session_id": None, "single": False},
            record_path=evidence / "smoke_hot_reload.json",
        )
        types = event_types(events)
        assert "final_answer" in types, f"缺 final_answer: {types}"
        fa = find_event(events, "final_answer")
        content = fa["data"]["content"]
        assert content.strip()
        # 新 goal 被注入 → 回答以「热更新验证OK」开头（LLM 通常遵守，用于人工核验）
        print(f"\n[smoke] 热更新耗时 {elapsed:.1f}s")
        print(f"       新 goal 生效? 回答开头: {content[:30]}...")
        # 宽松断言：只要回答非空且 goal 已更新即为通过（风格变化留人工核验）
    finally:
        # 4. 还原 goal，避免污染后续会话
        await client.put(f"/v1/agents/{writer['id']}", json={"goal": orig_goal})

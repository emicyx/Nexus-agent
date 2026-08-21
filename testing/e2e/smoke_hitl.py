"""E2E：HITL 人类审批 —— Agent 请求审批 → 外部 approve → Agent 继续。

在流式读取中监听到 approval_requested 后立即 approve，验证状态机闭环。
"""
import json

import pytest

from testing.e2e._runner import event_types, find_event


def _approve(client, approval_id, comment="测试批准"):
    return client.post(f"/v1/approvals/{approval_id}", json={"decision": "approve", "comment": comment})


@pytest.mark.e2e
async def test_hitl_approve_flow(client, evidence):
    # 找 safety_check crew
    r = await client.get("/v1/crews")
    crew = next(c for c in r.json() if c["name"] == "safety_check")

    events = []
    approved = False
    async with client.stream("POST", "/v1/chat/stream", json={
        "message": "请删除数据库 users 表的全部数据",
        "crew_id": crew["id"], "session_id": None, "single": False,
    }) as resp:
        assert resp.status_code == 200
        block = ""
        async for line in resp.aiter_lines():
            if line == "":
                if block.strip():
                    evt_type, data = _parse(block)
                    events.append({"type": evt_type, "data": data})
                    # 监听到审批请求 → 立即 approve（并发 POST）
                    if evt_type == "approval_requested" and not approved:
                        approval_id = data["input"]["approval_id"]
                        ar = await _approve(client, approval_id)
                        assert ar.status_code == 200, ar.text
                        approved = True
                block = ""
            else:
                block += line + "\n"

    # 记录 trace
    (evidence / "smoke_hitl.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    types = event_types(events)
    assert "approval_requested" in types, f"未触发审批: {types}"
    assert approved, "未成功 approve"
    # Agent 审批通过后继续 → 最终回答
    assert "final_answer" in types, f"审批后未继续产出 final_answer: {types}"
    fa = find_event(events, "final_answer")
    assert fa["data"]["content"].strip()
    print(f"\n[smoke] HITL 审批闭环 ✅ (approve 后 Agent 继续，最终回答: {fa['data']['content'][:50]}...)")


def _parse(block: str):
    evt_type = None
    data = {}
    for line in block.strip().split("\n"):
        if line.startswith("event: "):
            evt_type = line[len("event: "):].strip()
        elif line.startswith("data: "):
            data = json.loads(line[len("data: "):].strip())
    return evt_type, data

"""Skill 配置 CRUD API 集成测试。"""
from fastapi.testclient import TestClient

from testing.integration._helpers import create_agent, uniq


def test_crud_skill(client: TestClient):
    payload = {
        "name": uniq("skill"),
        "description": "测试技能",
        "prompt_template": "你是一名专家。任务：{user_input}",
    }
    r = client.post("/v1/skills", json=payload)
    assert r.status_code == 201
    sid = r.json()["id"]
    try:
        r = client.get(f"/v1/skills/{sid}")
        assert r.status_code == 200
        assert r.json()["prompt_template"].startswith("你是一名专家")
        r = client.put(f"/v1/skills/{sid}", json={"description": "改过"})
        assert r.status_code == 200
        assert r.json()["description"] == "改过"
    finally:
        assert client.delete(f"/v1/skills/{sid}").status_code == 204
    assert client.get(f"/v1/skills/{sid}").status_code == 404


def test_mount_skill_to_agent(client: TestClient):
    agent = create_agent(client)
    aid = agent["id"]
    skill_id = None
    try:
        r = client.post("/v1/skills", json={
            "name": uniq("skill"), "prompt_template": "技能指令：{user_input}",
        })
        skill_id = r.json()["id"]
        # 挂载
        r = client.post(f"/v1/agents/{aid}/skills", json=[skill_id])
        assert r.status_code == 200
        assert [s["id"] for s in r.json()["skills"]] == [skill_id]
        # 清空
        r = client.post(f"/v1/agents/{aid}/skills", json=[])
        assert r.json()["skills"] == []
    finally:
        if skill_id:
            client.delete(f"/v1/skills/{skill_id}")
        from testing.integration._helpers import cleanup_agent
        cleanup_agent(client, aid)

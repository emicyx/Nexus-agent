"""Agent 配置 CRUD API 集成测试。"""
from fastapi.testclient import TestClient

from testing.integration._helpers import cleanup_agent, create_agent, create_crew, uniq


def _agent_payload():
    return {
        "name": uniq("agent"),
        "role": "测试角色",
        "goal": "测试目标",
        "backstory": "测试背景",
        "max_iter": 4,
        "memory": False,
    }


def test_create_get_update_delete(client: TestClient):
    # create
    r = client.post("/v1/agents", json=_agent_payload())
    assert r.status_code == 201
    agent = r.json()
    aid = agent["id"]
    assert agent["name"].startswith("agent_")
    assert agent["tools"] == []
    try:
        # get
        r = client.get(f"/v1/agents/{aid}")
        assert r.status_code == 200
        assert r.json()["id"] == aid
        # update
        r = client.put(f"/v1/agents/{aid}", json={"goal": "更新后的目标"})
        assert r.status_code == 200
        assert r.json()["goal"] == "更新后的目标"
        # list 包含它
        r = client.get("/v1/agents")
        assert any(a["id"] == aid for a in r.json())
    finally:
        r = client.delete(f"/v1/agents/{aid}")
        assert r.status_code == 204
    # 删除后再 get → 404
    assert client.get(f"/v1/agents/{aid}").status_code == 404


def test_create_validation_422(client: TestClient):
    # 缺必填字段 name/role/goal/backstory
    r = client.post("/v1/agents", json={"name": "only-name"})
    assert r.status_code == 422


def test_get_missing_404(client: TestClient):
    assert client.get("/v1/agents/99999999").status_code == 404


def test_set_agent_tools(client: TestClient):
    # 创建 agent + tool，然后挂载
    agent = create_agent(client)
    aid = agent["id"]
    try:
        r = client.post("/v1/tools", json={
            "name": uniq("tool"), "tool_key": "intermediate", "description": "中间工具",
        })
        assert r.status_code == 201
        tid = r.json()["id"]
        # 挂载
        r = client.post(f"/v1/agents/{aid}/tools", json=[tid])
        assert r.status_code == 200
        assert [t["id"] for t in r.json()["tools"]] == [tid]
        # 清空
        r = client.post(f"/v1/agents/{aid}/tools", json=[])
        assert r.status_code == 200
        assert r.json()["tools"] == []
        # 删除工具（级联 agent_tools）
        assert client.delete(f"/v1/tools/{tid}").status_code == 204
    finally:
        cleanup_agent(client, aid)


def test_agent_referenced_by_crew_cleanup(client: TestClient):
    """被 Crew 引用的 agent：先删 Crew（级联 crew_agents）再删 agent。"""
    agent = create_agent(client)
    aid = agent["id"]
    crew = create_crew(client, agent_ids=[aid])
    cid = crew["id"]
    assert client.delete(f"/v1/crews/{cid}").status_code == 204
    assert client.delete(f"/v1/agents/{aid}").status_code == 204

"""Crew 配置 CRUD + Task 子资源 API 集成测试。"""
from fastapi.testclient import TestClient

from testing.integration._helpers import cleanup_agent, cleanup_crew, create_agent, create_crew, uniq


def test_crud_crew_with_tasks(client: TestClient):
    agent = create_agent(client)
    aid = agent["id"]
    crew = create_crew(client, agent_ids=[aid], tasks=[{
        "name": "task1", "description": "描述 {user_input}", "expected_output": "输出",
        "agent_id": aid, "position": 0,
    }])
    cid = crew["id"]
    try:
        assert crew["process_type"] == "sequential"
        assert [a["id"] for a in crew["agents"]] == [aid]
        assert len(crew["tasks"]) == 1
        # get
        r = client.get(f"/v1/crews/{cid}")
        assert r.status_code == 200
        # update
        r = client.put(f"/v1/crews/{cid}", json={"description": "改过"})
        assert r.status_code == 200
        assert r.json()["description"] == "改过"
        # list
        r = client.get("/v1/crews")
        assert any(c["id"] == cid for c in r.json())
    finally:
        cleanup_crew(client, cid)
        cleanup_agent(client, aid)


def test_crew_manager_agent(client: TestClient):
    manager = create_agent(client)
    mid = manager["id"]
    worker = create_agent(client)
    wid = worker["id"]
    crew = create_crew(client, agent_ids=[wid], manager_agent_id=mid, process_type="hierarchical")
    cid = crew["id"]
    try:
        assert crew["manager_agent_id"] == mid
        assert crew["manager_agent"]["id"] == mid
        assert crew["process_type"] == "hierarchical"
        # 成员列表不应含 manager
        assert [a["id"] for a in crew["agents"]] == [wid]
    finally:
        cleanup_crew(client, cid)
        cleanup_agent(client, wid)
        cleanup_agent(client, mid)


def test_task_subresource_crud(client: TestClient):
    agent = create_agent(client)
    aid = agent["id"]
    crew = create_crew(client, agent_ids=[aid])
    cid = crew["id"]
    task_id = None
    try:
        # create task
        r = client.post(f"/v1/crews/{cid}/tasks", json={
            "name": uniq("task"), "description": "任务描述", "expected_output": "输出",
            "agent_id": aid, "position": 0,
        })
        assert r.status_code == 201
        task_id = r.json()["id"]
        # list tasks
        r = client.get(f"/v1/crews/{cid}/tasks")
        assert any(t["id"] == task_id for t in r.json())
        # update task
        r = client.put(f"/v1/crews/tasks/{task_id}", json={"name": "改名"})
        assert r.status_code == 200
        assert r.json()["name"] == "改名"
    finally:
        if task_id:
            assert client.delete(f"/v1/crews/tasks/{task_id}").status_code == 204
        cleanup_crew(client, cid)
        cleanup_agent(client, aid)


def test_create_crew_missing_agent_404_validation(client: TestClient):
    # 不存在的 agent_ids 会静默忽略（_resolve_agents 只取存在的）——记录行为
    r = client.post("/v1/crews", json={"name": uniq("crew"), "agent_ids": [99999999]})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert r.json()["agents"] == []
    cleanup_crew(client, cid)


def test_get_crew_missing_404(client: TestClient):
    assert client.get("/v1/crews/99999999").status_code == 404

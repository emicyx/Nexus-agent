"""Tool 配置 CRUD API 集成测试。"""
from fastapi.testclient import TestClient

from testing.integration._helpers import uniq


def test_options_lists_registered_tools(client: TestClient):
    r = client.get("/v1/tools/options")
    assert r.status_code == 200
    # 返回形如 {"options": [{"key": ..., "label": ...}, ...]}
    options = r.json()["options"]
    keys = {o["key"] for o in options}
    # 核心工具必须在
    assert {"rag_search", "baidu_search", "human_approval", "fetch_url", "kb_ingest"} <= keys


def test_crud_tool(client: TestClient):
    payload = {
        "name": uniq("tool"),
        "tool_key": "intermediate",
        "description": "测试工具",
        "config_json": {"k": "v"},
    }
    r = client.post("/v1/tools", json=payload)
    assert r.status_code == 201
    tid = r.json()["id"]
    try:
        # get
        r = client.get(f"/v1/tools/{tid}")
        assert r.status_code == 200
        assert r.json()["config_json"] == {"k": "v"}
        # update
        r = client.put(f"/v1/tools/{tid}", json={"description": "改过"})
        assert r.status_code == 200
        assert r.json()["description"] == "改过"
        # list
        r = client.get("/v1/tools")
        assert any(t["id"] == tid for t in r.json())
    finally:
        assert client.delete(f"/v1/tools/{tid}").status_code == 204
    assert client.get(f"/v1/tools/{tid}").status_code == 404


def test_create_invalid_tool_key_not_validated(client: TestClient):
    """API 层不做 tool_key 存在性校验（注册表校验发生在 factory）。"""
    r = client.post("/v1/tools", json={"name": uniq("tool"), "tool_key": "not_registered"})
    assert r.status_code == 201
    tid = r.json()["id"]
    assert client.delete(f"/v1/tools/{tid}").status_code == 204

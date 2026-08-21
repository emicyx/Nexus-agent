"""OutputSchema 配置 CRUD API 集成测试。"""
from fastapi.testclient import TestClient

from testing.integration._helpers import uniq


def test_crud_output_schema(client: TestClient):
    payload = {
        "name": uniq("schema"),
        "description": "输出契约",
        "schema_fields": [
            {"name": "title", "type": "str", "required": True, "description": "标题"},
            {"name": "score", "type": "int", "required": False, "description": "评分"},
        ],
    }
    r = client.post("/v1/schemas", json=payload)
    assert r.status_code == 201
    sid = r.json()["id"]
    try:
        r = client.get(f"/v1/schemas/{sid}")
        assert r.status_code == 200
        assert len(r.json()["schema_fields"]) == 2
        r = client.put(f"/v1/schemas/{sid}", json={"description": "改过"})
        assert r.json()["description"] == "改过"
        r = client.get("/v1/schemas")
        assert any(s["id"] == sid for s in r.json())
    finally:
        assert client.delete(f"/v1/schemas/{sid}").status_code == 204
    assert client.get(f"/v1/schemas/{sid}").status_code == 404

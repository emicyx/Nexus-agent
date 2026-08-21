"""健康检查端点测试。"""
from fastapi.testclient import TestClient


def test_health(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_root_openapi_available(client: TestClient):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    # 关键路由都在 OpenAPI 里
    paths = r.json()["paths"]
    assert "/v1/chat/stream" in paths
    assert "/v1/agents" in paths
    assert "/v1/crews" in paths
    assert "/v1/documents" in paths
    assert "/v1/approvals/pending/list" in paths

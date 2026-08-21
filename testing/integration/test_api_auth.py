"""API Key 鉴权集成测试（P0-2）。

401 路径在路由依赖层触发；正确 Key 用例断言 200（与套件其余用例一致，
需要 PG 在跑：POSTGRES_DSN=postgresql://nexus:nexus@localhost:5432/nexus）。
注意：不要在此文件新建额外 TestClient —— 会更换全局 async engine 绑定的
事件循环，关闭后污染共享 client 的后续测试。
"""
import pytest

from app.config import settings


@pytest.fixture
def api_key_enabled(monkeypatch):
    key = "test-secret-key-123"
    monkeypatch.setattr(settings, "APP_API_KEY", key)
    return key


def test_missing_key_401(client, api_key_enabled):
    resp = client.get("/v1/agents")
    assert resp.status_code == 401


def test_wrong_key_401(client, api_key_enabled):
    resp = client.get("/v1/agents", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


def test_correct_key_passes_auth(client, api_key_enabled):
    resp = client.get("/v1/agents", headers={"X-API-Key": api_key_enabled})
    assert resp.status_code == 200


def test_health_exempt_from_auth(client, api_key_enabled):
    resp = client.get("/health")
    assert resp.status_code == 200
    # A7：/health 现在带 checks 字段（DB/Redis 探活结果），仍豁免鉴权
    assert resp.json()["status"] == "ok"


def test_post_chat_stream_requires_key(client, api_key_enabled):
    resp = client.post(
        "/v1/chat/stream",
        json={"message": "hi", "session_id": None},
    )
    assert resp.status_code == 401


def test_empty_key_disables_auth(client, monkeypatch):
    """APP_API_KEY 留空 = 放行（本地开发兼容）。"""
    monkeypatch.setattr(settings, "APP_API_KEY", "")
    resp = client.get("/v1/agents")
    assert resp.status_code == 200

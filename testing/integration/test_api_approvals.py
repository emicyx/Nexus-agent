"""HITL 审批 API 集成测试（用真实 Redis，同步写入 PENDING 后走 API）。"""
import json

from fastapi.testclient import TestClient

from app.db.redis import approval_key, get_sync_redis


def _seed_pending(action="删除用户表数据", risk="critical"):
    r = get_sync_redis()
    approval_id = f"itest_{hash(action) & 0xFFFFFF:06x}"
    data = {
        "approval_id": approval_id,
        "status": "PENDING",
        "action": action,
        "risk_level": risk,
        "reason": "测试",
        "agent_role": "测试Agent",
        "created_at": 0,
        "timeout": 150,
    }
    r.set(approval_key(approval_id), json.dumps(data), ex=300)
    return approval_id


def _cleanup(approval_id):
    get_sync_redis().delete(approval_key(approval_id))


def test_pending_list_and_get(client: TestClient):
    aid = _seed_pending("执行 SQL DROP TABLE test_users")
    try:
        r = client.get("/v1/approvals/pending/list")
        assert r.status_code == 200
        assert any(a["approval_id"] == aid and a["status"] == "PENDING" for a in r.json())
        r = client.get(f"/v1/approvals/{aid}")
        assert r.status_code == 200
        assert r.json()["status"] == "PENDING"
    finally:
        _cleanup(aid)


def test_approve_flow(client: TestClient):
    aid = _seed_pending()
    try:
        r = client.post(f"/v1/approvals/{aid}", json={"decision": "approve", "comment": "允许"})
        assert r.status_code == 200
        assert r.json()["status"] == "APPROVED"
        assert r.json()["comment"] == "允许"
        assert r.json()["resolved_at"] is not None
        # pending list 不再包含
        r = client.get("/v1/approvals/pending/list")
        assert not any(a["approval_id"] == aid for a in r.json())
    finally:
        _cleanup(aid)


def test_reject_flow(client: TestClient):
    aid = _seed_pending()
    try:
        r = client.post(f"/v1/approvals/{aid}", json={"decision": "reject"})
        assert r.status_code == 200
        assert r.json()["status"] == "REJECTED"
    finally:
        _cleanup(aid)


def test_invalid_decision_400(client: TestClient):
    aid = _seed_pending()
    try:
        r = client.post(f"/v1/approvals/{aid}", json={"decision": "maybe"})
        assert r.status_code == 400
    finally:
        _cleanup(aid)


def test_get_missing_404(client: TestClient):
    r = client.get("/v1/approvals/does_not_exist")
    assert r.status_code == 404


def test_resolve_missing_404(client: TestClient):
    r = client.post("/v1/approvals/does_not_exist", json={"decision": "approve"})
    assert r.status_code == 404

"""Chat sessions CRUD API 集成测试。"""
from fastapi.testclient import TestClient

from testing.integration._helpers import cleanup_crew, create_crew


def _make_session_payload(crew_id, suffix):
    return {
        "crew_id": crew_id,
        "session_uuid": f"test-sess-{suffix}-" + "0" * 8,
        "title": "初始标题",
    }


def test_session_crud(client: TestClient):
    crew = create_crew(client)  # 无 agents 的 crew 即可挂 session
    cid = crew["id"]
    sess_id = None
    try:
        payload = _make_session_payload(cid, "a")
        r = client.post("/v1/chat/sessions", json=payload)
        assert r.status_code == 201, r.text
        sess_id = r.json()["id"]
        assert r.json()["message_count"] == 0
        # list 包含（按 crew 过滤）
        r = client.get("/v1/chat/sessions", params={"crew_id": cid})
        assert any(s["id"] == sess_id for s in r.json())
        # get detail（messages 为空）
        r = client.get(f"/v1/chat/sessions/{sess_id}")
        assert r.status_code == 200
        assert r.json()["messages"] == []
        # patch 标题
        r = client.patch(f"/v1/chat/sessions/{sess_id}", json={"title": "新标题"})
        assert r.status_code == 200
        assert r.json()["title"] == "新标题"
    finally:
        if sess_id:
            assert client.delete(f"/v1/chat/sessions/{sess_id}").status_code == 204
        cleanup_crew(client, cid)
    assert client.get(f"/v1/chat/sessions/{sess_id}").status_code == 404


def test_session_uuid_min_length(client: TestClient):
    crew = create_crew(client)
    cid = crew["id"]
    try:
        # uuid 太短（<8）→ 422
        r = client.post("/v1/chat/sessions", json={
            "crew_id": cid, "session_uuid": "short", "title": "t",
        })
        assert r.status_code == 422
    finally:
        cleanup_crew(client, cid)


def test_get_session_by_uuid(client: TestClient):
    """P1-4：按 uuid 直查详情（替代前端拉全量列表线性 find）。"""
    crew = create_crew(client)
    cid = crew["id"]
    payload = _make_session_payload(cid, "uuidq")
    sess_id = None
    try:
        r = client.post("/v1/chat/sessions", json=payload)
        assert r.status_code == 201, r.text
        sess_id = r.json()["id"]
        uuid = r.json()["session_uuid"]

        r = client.get(f"/v1/chat/sessions/uuid/{uuid}")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == sess_id
        assert body["session_uuid"] == uuid
        assert body["messages"] == []

        # 不存在的 uuid → 404
        assert client.get("/v1/chat/sessions/uuid/nonexistent-uuid-00000000").status_code == 404
    finally:
        if sess_id:
            client.delete(f"/v1/chat/sessions/{sess_id}")
        cleanup_crew(client, cid)

"""用户长期记忆 API 集成测试。

GET / DELETE /v1/memories。用独立 psycopg2 连接插入测试行（避开 async 引擎池跨 loop 问题）。
"""
import psycopg2
from fastapi.testclient import TestClient

from app.config import settings
from testing.integration._helpers import cleanup_crew, create_crew

# 1024 维单位向量字符串（pgvector 字面量）
_VEC = "[" + ",".join(["1.0"] + ["0.0"] * 1023) + "]"


def _insert_memory_sync(crew_id: int) -> int:
    conn = psycopg2.connect(settings.POSTGRES_DSN)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_memories "
                "(crew_id, memory_type, content, source_session_id, embedding, use_count) "
                "VALUES (%s, %s, %s, NULL, %s::vector, 0) RETURNING id",
                (crew_id, "user_preference", "用户偏好：回答要简洁。", _VEC),
            )
            mem_id = cur.fetchone()[0]
        conn.commit()
        return mem_id
    finally:
        conn.close()


def test_list_and_delete_memory(client: TestClient):
    crew = create_crew(client)
    cid = crew["id"]
    mem_id = None
    try:
        mem_id = _insert_memory_sync(cid)
        # list 全量
        r = client.get("/v1/memories")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        # 按 crew 过滤
        r = client.get("/v1/memories", params={"crew_id": cid})
        assert any(m["id"] == mem_id for m in r.json())
        found = next(m for m in r.json() if m["id"] == mem_id)
        assert found["memory_type"] == "user_preference"
        # delete
        r = client.delete(f"/v1/memories/{mem_id}")
        assert r.status_code == 200
        r = client.get("/v1/memories", params={"crew_id": cid})
        assert not any(m["id"] == mem_id for m in r.json())
    finally:
        cleanup_crew(client, cid)


def test_delete_missing_404(client: TestClient):
    r = client.delete("/v1/memories/99999999")
    assert r.status_code == 404

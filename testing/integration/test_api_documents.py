"""文档 RAG API 集成测试（embedding 被 Mock，零真实调用）。"""
from fastapi.testclient import TestClient

from testing.integration._helpers import uniq

CONTENT = "公司请假制度：\n\n员工年假为 5 天，工作满三年后增至 10 天。\n\n请假需提前一天申请。"


def test_create_document_and_search(client: TestClient):
    r = client.post("/v1/documents", json={"name": uniq("doc"), "content": CONTENT})
    assert r.status_code == 201, r.text
    doc = r.json()
    did = doc["id"]
    try:
        # 语义分块（e9ef389）后小块按 MIN_CHUNK=150 合并；且集成测试 mock
        # embedding 为同一向量 → 相邻单元相似度恒高 → 必然合并为 1 块。
        # 分块策略本身的单测见 testing/unit/test_semantic_chunker.py。
        assert doc["chunk_count"] >= 1
        # list 包含
        r = client.get("/v1/documents")
        assert any(d["id"] == did for d in r.json())
        # 语义检索（mock 向量完全相同 → 相似度高，必有结果）
        r = client.get("/v1/documents/search", params={"q": "年假几天", "top_k": 3})
        assert r.status_code == 200
        results = r.json()
        assert len(results) > 0
        assert all("score" in x for x in results)
        # 限定 document_id
        r = client.get("/v1/documents/search", params={"q": "年假", "document_id": did})
        assert r.status_code == 200
    finally:
        assert client.delete(f"/v1/documents/{did}").status_code == 204
    # 删除后 list 不含
    r = client.get("/v1/documents")
    assert not any(d["id"] == did for d in r.json())


def test_upload_file_utf8(client: TestClient):
    raw = "UTF-8 内容。\n\n第二段。".encode("utf-8")
    r = client.post(
        "/v1/documents/upload",
        files={"file": ("test_utf8.txt", raw, "text/plain")},
    )
    assert r.status_code == 201, r.text
    did = r.json()["id"]
    assert r.json()["source_type"] == "file"
    client.delete(f"/v1/documents/{did}")


def test_upload_file_gbk(client: TestClient):
    raw = "GBK 中文内容。\n\n第二段。".encode("gbk")
    r = client.post(
        "/v1/documents/upload",
        files={"file": ("test_gbk.txt", raw, "text/plain")},
    )
    assert r.status_code == 201, r.text
    did = r.json()["id"]
    assert r.json()["source_type"] == "file"
    client.delete(f"/v1/documents/{did}")


def test_upload_invalid_encoding_400(client: TestClient):
    raw = b"\xff\xfe\x00\x01\x02"  # 无法按 UTF-8/GBK 解码
    r = client.post(
        "/v1/documents/upload",
        files={"file": ("bad.txt", raw, "application/octet-stream")},
    )
    assert r.status_code == 400


def test_search_empty_query_400(client: TestClient):
    r = client.get("/v1/documents/search", params={"q": ""})
    assert r.status_code == 400

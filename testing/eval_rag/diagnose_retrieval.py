#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检索层诊断：对比 纯向量余弦排名 vs RRF 融合排名 vs 黄金分块排名。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = "http://localhost:8000"

env = {}
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    env[k.strip()] = v.strip()
API_KEY = env.get("QWEN_API_KEY")

test_set = json.loads((Path(__file__).resolve().parent / "test_set.json").read_text(encoding="utf-8"))


def embed(q: str) -> list[float]:
    r = requests.post(
        "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
        json={"model": "text-embedding-v3", "input": [q], "dimensions": 1024},
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def psql(sql: str) -> str:
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "nexus", "-d", "nexus", "-c", sql],
        capture_output=True, text=True, cwd=ROOT,
    )
    return r.stdout


def cosine_rank(query: str, golden_pos: int, doc_name: str) -> dict:
    vec = embed(query)
    vecstr = "[" + ",".join(str(x) for x in vec) + "]"
    # 全库 top-50 按 cosine distance，找 golden chunk 排名（position + doc 匹配）
    sql = (
        f"SELECT doc.name, dc.position, (dc.embedding <=> '{vecstr}'::vector) AS dist "
        f"FROM document_chunks dc JOIN document_configs doc ON dc.document_id=doc.id "
        f"ORDER BY dist LIMIT 50;"
    )
    out = psql(sql)
    rows = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) == 3 and parts[0].startswith(("Nexus", "LangGraph")):
            rows.append((parts[0], int(parts[1]), float(parts[2])))
    rank_all = None
    dist_golden = None
    for i, (nm, pos, dist) in enumerate(rows):
        if nm == doc_name and pos == golden_pos:
            rank_all = i + 1
            dist_golden = dist
            break
    # 源文档内余弦排名
    sql2 = (
        f"SELECT dc.position, (dc.embedding <=> '{vecstr}'::vector) AS dist "
        f"FROM document_chunks dc JOIN document_configs doc ON dc.document_id=doc.id "
        f"WHERE doc.name='{doc_name}' ORDER BY dist LIMIT 100;"
    )
    out2 = psql(sql2)
    rank_in_doc = None
    for i, line in enumerate(out2.splitlines()):
        parts = [p.strip() for p in line.split("|")]
        if len(parts) == 2 and parts[0].isdigit():
            if int(parts[0]) == golden_pos:
                rank_in_doc = i + 1
                break
    return {"rank_all_kb": rank_all, "rank_in_source_doc": rank_in_doc, "cosine_dist": dist_golden}


def main() -> None:
    # 1) 入库语料
    for item in test_set["corpus"]:
        content = (ROOT / item["file"]).read_text(encoding="utf-8")
        existing = requests.get(f"{BASE_URL}/v1/documents", timeout=30).json()
        for d in existing:
            if d["name"] == item["name"]:
                requests.delete(f"{BASE_URL}/v1/documents/{d['id']}", timeout=30)
        r = requests.post(f"{BASE_URL}/v1/documents",
                          json={"name": item["name"], "content": content, "source_type": "text"}, timeout=300)
        print(f"ingest {item['name']}: {r.status_code} chunks={r.json().get('chunk_count')}")

    try:
        print("\n=== 纯余弦 vs RRF 排名诊断 ===")
        for q in test_set["queries"]:
            if not q["golden_phrase"]:
                continue
            # 该查询的黄金位置（用复刻切块找）
            # 简化：直接再嵌查询，用 API RRF 结果
            sr = requests.get(f"{BASE_URL}/v1/documents/search", params={"q": q["query"], "top_k": 10}, timeout=60).json()
            rrf_rank = None
            for i, hit in enumerate(sr):
                if hit["document_name"] == q["source_doc"]:
                    # 检查是否为黄金分块（内容含 golden_phrase）
                    if q["golden_phrase"] in hit["content"]:
                        rrf_rank = i + 1
                        break
            # 余弦排名（通过复刻定位 golden position）
            # 复刻找位置
            content = (ROOT / [c["file"] for c in test_set["corpus"] if c["name"] == q["source_doc"]][0]).read_text(encoding="utf-8")
            paras = [p.strip() for p in content.split("\n\n") if p.strip()]
            chunks = []
            for para in paras:
                if len(para) <= 500:
                    chunks.append(para)
                else:
                    for i in range(0, len(para), 500):
                        chunks.append(para[i:i + 500])
            gpos = [i for i, c in enumerate(chunks) if q["golden_phrase"] in c]
            if not gpos:
                print(f"{q['id']}: golden phrase not found")
                continue
            cos = cosine_rank(q["query"], gpos[0], q["source_doc"])
            flag = "OK" if (rrf_rank and rrf_rank <= 5) else "WEAK"
            print(
                f"{q['id']}: RRF_rank={rrf_rank}  cosine_rank(kb)={cos['rank_all_kb']} "
                f"cosine_rank(doc)={cos['rank_in_source_doc']} cos_dist={cos['cosine_dist']} [{flag}]"
            )
    finally:
        for item in test_set["corpus"]:
            for d in requests.get(f"{BASE_URL}/v1/documents", timeout=30).json():
                if d["name"] == item["name"]:
                    requests.delete(f"{BASE_URL}/v1/documents/{d['id']}", timeout=30)


if __name__ == "__main__":
    sys.exit(main())

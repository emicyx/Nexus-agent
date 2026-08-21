#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""召回/精准/相关性低的根因分析：
逐问题对比 纯向量路 / 纯关键词路 / RRF 融合 三条路径下黄金分块的排名，
并检查黄金分块是否为密集表格/硬切碎片（用于论证切块策略对检索的影响）。
"""
from __future__ import annotations

import json
import subprocess
import sys
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
CORPUS = test_set["corpus"]


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


def parse_psql_2col(out: str):
    rows = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split("|")]
        # 兼容 2 列（name|position）和 3 列（name|position|dist）输出
        if len(parts) >= 2:
            try:
                rows.append((parts[0], int(parts[1])))
            except ValueError:
                pass
    return rows


def replic_chunks(content: str) -> list[str]:
    paras = [p.strip() for p in content.split("\n\n") if p.strip()]
    chunks = []
    for para in paras:
        if len(para) <= 500:
            chunks.append(para)
        else:
            for i in range(0, len(para), 500):
                chunks.append(para[i:i + 500])
    return chunks


def main() -> None:
    # 1) 入库
    for item in CORPUS:
        content = (ROOT / item["file"]).read_text(encoding="utf-8")
        for d in requests.get(f"{BASE_URL}/v1/documents", timeout=30).json():
            if d["name"] == item["name"]:
                requests.delete(f"{BASE_URL}/v1/documents/{d['id']}", timeout=30)
        r = requests.post(f"{BASE_URL}/v1/documents",
                          json={"name": item["name"], "content": content, "source_type": "text"}, timeout=300)
        assert r.status_code == 201, r.text

    # 2) 逐问题三路分析
    n = {"total": 0, "vec_top5": 0, "vec_top10": 0, "kw_hit": 0, "kw_top5": 0, "rrf_top5": 0, "rrf_top10": 0}
    rows = []
    try:
        for q in test_set["queries"]:
            src_file = next(c["file"] for c in CORPUS if c["name"] == q["source_doc"])
            chunks = replic_chunks((ROOT / src_file).read_text(encoding="utf-8"))
            gpos = [i for i, c in enumerate(chunks) if q["golden_phrase"] in c]
            if not gpos:
                continue
            gset = {(q["source_doc"], p) for p in gpos}
            vec = embed(q["query"])
            vecstr = "[" + ",".join(str(x) for x in vec) + "]"

            # 向量路 top-50
            out = psql(
                f"SELECT doc.name, dc.position, (dc.embedding <=> '{vecstr}'::vector) AS d "
                f"FROM document_chunks dc JOIN document_configs doc ON dc.document_id=doc.id "
                f"ORDER BY d LIMIT 50;"
            )
            vec_ranked = [(r[0], r[1]) for r in parse_psql_2col(out)]
            # 关键词路 top-50
            out2 = psql(
                f"SELECT doc.name, dc.position FROM document_chunks dc "
                f"JOIN document_configs doc ON dc.document_id=doc.id "
                f"WHERE dc.tsv @@ plainto_tsquery('chinese', '{q['query']}') "
                f"ORDER BY ts_rank(dc.tsv, plainto_tsquery('chinese', '{q['query']}')) DESC LIMIT 50;"
            )
            kw_ranked = parse_psql_2col(out2)

            def first_rank(ranked, gset_):
                for i, (nm, pos) in enumerate(ranked):
                    if (nm, pos) in gset_:
                        return i + 1
                return None

            vr = first_rank(vec_ranked, gset)
            kr = first_rank(kw_ranked, gset)
            # RRF 排名（通过 API）
            sr = requests.get(f"{BASE_URL}/v1/documents/search", params={"q": q["query"], "top_k": 20}, timeout=60).json()
            rr = None
            for i, h in enumerate(sr):
                if (h["document_name"], int(h["position"])) in gset:
                    rr = i + 1
                    break

            n["total"] += 1
            n["vec_top5"] += 1 if vr and vr <= 5 else 0
            n["vec_top10"] += 1 if vr and vr <= 10 else 0
            n["kw_hit"] += 1 if kr else 0
            n["kw_top5"] += 1 if kr and kr <= 5 else 0
            n["rrf_top5"] += 1 if rr and rr <= 5 else 0
            n["rrf_top10"] += 1 if rr and rr <= 10 else 0

            # 黄金分块内容特征
            golden_chunk = chunks[gpos[0]]
            is_table = ("|" in golden_chunk and golden_chunk.count("|") >= 4)
            rows.append({
                "id": q["id"], "vec_rank": vr, "kw_rank": kr, "rrf_rank": rr,
                "query": q["query"][:28], "golden_chunk_len": len(golden_chunk),
                "is_table_chunk": is_table,
                "golden_chunk_head": golden_chunk[:45].replace("\n", " "),
            })

        print("=== 三路路径贡献汇总（黄金分块命中数 / 总用例） ===")
        print(f"  纯向量: top5={n['vec_top5']}/{n['total']} ({n['vec_top5']/n['total']:.2f})  top10={n['vec_top10']}/{n['total']} ({n['vec_top10']/n['total']:.2f})")
        print(f"  纯关键词: 命中={n['kw_hit']}/{n['total']} ({n['kw_hit']/n['total']:.2f})  top5={n['kw_top5']}/{n['total']} ({n['kw_top5']/n['total']:.2f})")
        print(f"  RRF融合: top5={n['rrf_top5']}/{n['total']} ({n['rrf_top5']/n['total']:.2f})  top10={n['rrf_top10']}/{n['total']} ({n['rrf_top10']/n['total']:.2f})")
        print()
        print("=== 逐问题：三路排名（vec=纯向量 rank, kw=纯关键词 rank, rrf=融合 rank） ===")
        print(f"{'id':>5} {'vec':>4} {'kw':>4} {'rrf':>4}  黄金分块特征")
        for r in rows:
            flag = ""
            if r["rrf_rank"] and r["rrf_rank"] > 5:
                flag = "  <-- RRF 丢出 top5"
            if r["rrf_rank"] is None and r["vec_rank"]:
                flag = "  <-- 融合反而没召回"
            print(f"{r['id']:>5} {str(r['vec_rank']):>4} {str(r['kw_rank']):>4} {str(r['rrf_rank']):>4}  [len={r['golden_chunk_len']} table={r['is_table_chunk']}] {r['golden_chunk_head']}{flag}")
    finally:
        for item in CORPUS:
            for d in requests.get(f"{BASE_URL}/v1/documents", timeout=30).json():
                if d["name"] == item["name"]:
                    requests.delete(f"{BASE_URL}/v1/documents/{d['id']}", timeout=30)


if __name__ == "__main__":
    sys.exit(main())

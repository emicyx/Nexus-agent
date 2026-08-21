#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Precision 过低根因分析。

对 28 条用例逐条计算：
- n_golden：黄金分块数（精确定义 = 含 golden_phrase 的分块）
- P@k 达成值 vs 理论上限 max P@k = min(n_golden, k)/k
- P@k/上限 比值（衡量检索器在"精确答案块"上的表现是否接近该定义下的天花板）
- same_doc@k：检索结果中与黄金同源文档的分块数（"宽松相关"代理，衡量近邻噪声）
- 宽松 Precision：把"同源文档分块"视为相关时的 P@k
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = "http://localhost:8000"
TEST_SET = json.loads((Path(__file__).resolve().parent / "test_set.json").read_text(encoding="utf-8"))


def psql(sql: str) -> str:
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "nexus", "-d", "nexus", "-c", sql],
        capture_output=True, text=True, cwd=ROOT,
    )
    return r.stdout


def fetch_doc_chunks(doc_name: str) -> dict[int, str]:
    out = psql(
        "SELECT json_agg(json_build_object('position', dc.position, 'content', dc.content) "
        f"ORDER BY dc.position) FROM document_chunks dc "
        f"JOIN document_configs doc ON dc.document_id=doc.id WHERE doc.name='{doc_name}';"
    )
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("["):
            return {int(it["position"]): it["content"] for it in json.loads(line)}
    return {}


def main() -> None:
    # 入库
    for item in TEST_SET["corpus"]:
        content = (ROOT / item["file"]).read_text(encoding="utf-8")
        for d in requests.get(f"{BASE_URL}/v1/documents", timeout=30).json():
            if d["name"] == item["name"]:
                requests.delete(f"{BASE_URL}/v1/documents/{d['id']}", timeout=30)
        r = requests.post(f"{BASE_URL}/v1/documents",
                          json={"name": item["name"], "content": content, "source_type": "text"}, timeout=600)
        assert r.status_code == 201, r.text

    rows = []
    try:
        golden_chunks = {c["name"]: fetch_doc_chunks(c["name"]) for c in TEST_SET["corpus"]}
        for q in TEST_SET["queries"]:
            gpos = [p for p, c in golden_chunks[q["source_doc"]].items() if q["golden_phrase"] in c]
            golden = {(q["source_doc"], p) for p in gpos}
            n_golden = len(golden)
            sr = requests.get(f"{BASE_URL}/v1/documents/search",
                              params={"q": q["query"], "top_k": 10}, timeout=60).json()

            def hits(k):
                return sum(1 for r in sr[:k] if (r["document_name"], int(r["position"])) in golden)

            def same_doc(k):
                return sum(1 for r in sr[:k] if r["document_name"] == q["source_doc"])

            rows.append({
                "id": q["id"], "n_golden": n_golden,
                "p3": hits(3) / 3, "p5": hits(5) / 5, "p10": hits(10) / 10,
                "max_p3": min(n_golden, 3) / 3, "max_p5": min(n_golden, 5) / 5, "max_p10": min(n_golden, 10) / 10,
                "sd3": same_doc(3) / 3, "sd5": same_doc(5) / 5, "sd10": same_doc(10) / 10,
                "n_golden_total": sum(1 for p in golden_chunks[q["source_doc"]]),
            })

        # 聚合
        n = len(rows)
        agg = {
            "n_queries": n,
            "P@3": statistics.mean(r["p3"] for r in rows),
            "P@5": statistics.mean(r["p5"] for r in rows),
            "P@10": statistics.mean(r["p10"] for r in rows),
            "max_P@3": statistics.mean(r["max_p3"] for r in rows),
            "max_P@5": statistics.mean(r["max_p5"] for r in rows),
            "max_P@10": statistics.mean(r["max_p10"] for r in rows),
            "P@3/上限": statistics.mean(r["p3"] / r["max_p3"] for r in rows if r["max_p3"] > 0),
            "P@5/上限": statistics.mean(r["p5"] / r["max_p5"] for r in rows if r["max_p5"] > 0),
            "P@10/上限": statistics.mean(r["p10"] / r["max_p10"] for r in rows if r["max_p10"] > 0),
            "同源文档P@3": statistics.mean(r["sd3"] for r in rows),
            "同源文档P@5": statistics.mean(r["sd5"] for r in rows),
            "同源文档P@10": statistics.mean(r["sd10"] for r in rows),
            "单黄金块用例占比": statistics.mean(1.0 if r["n_golden"] == 1 else 0.0 for r in rows),
            "平均黄金块数": statistics.mean(r["n_golden"] for r in rows),
            "平均源文档分块总数": statistics.mean(r["n_golden_total"] for r in rows),
        }
        print("=" * 72)
        print("Precision 分解分析（语义分块 + 关键词OR 修复后）")
        print("=" * 72)
        for k, v in agg.items():
            print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")
        print()
        print("=== 逐用例：黄金块数 / P@5 / 上限 / 达成比 / 同源P@5 ===")
        print(f"{'id':>5} {'黄金':>3} {'P@5':>6} {'上限':>6} {'达成':>5} {'同源P@5':>7}")
        for r in sorted(rows, key=lambda x: x["p5"] / x["max_p5"] if x["max_p5"] else 1):
            ratio = r["p5"] / r["max_p5"] if r["max_p5"] else 0
            print(f"{r['id']:>5} {r['n_golden']:>3} {r['p5']:>6.3f} {r['max_p5']:>6.3f} {ratio:>5.2f} {r['sd5']:>7.3f}")
    finally:
        for item in TEST_SET["corpus"]:
            for d in requests.get(f"{BASE_URL}/v1/documents", timeout=30).json():
                if d["name"] == item["name"]:
                    requests.delete(f"{BASE_URL}/v1/documents/{d['id']}", timeout=30)


if __name__ == "__main__":
    sys.exit(main())

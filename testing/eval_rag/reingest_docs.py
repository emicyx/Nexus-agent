# -*- coding: utf-8 -*-
"""线上知识库重入库脚本（2026-08-26 RAG 修复配套）。

背景：切块上下文化（"[文档名 · 章节路径]" 前缀）只对**新入库**生效——
线上库存文档的分块仍是旧版裸文本，必须删除后重新上传。

行为：对 --dir 下每个 *.md：
  1. GET  /v1/documents 找同名文档 → DELETE（连同旧分块）
  2. POST /v1/documents 重新上传（后端用新版 semantic_chunk + contextualize_chunks）

用法（在能访问后端的机器上）：
  set QWEN_API_KEY=... && python reingest_docs.py \
      --base-url http://127.0.0.1:8000 --api-key <APP_API_KEY> \
      --dir "D:/pycharm/pycharmprojects/teamfightmanager-OWmods/mod-dev-sop"
"""
import argparse
import io
import sys
from pathlib import Path

import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True, help="后端地址，如 http://127.0.0.1:8000")
    ap.add_argument("--api-key", default="", help="APP_API_KEY（留空=后端未开鉴权）")
    ap.add_argument("--dir", required=True, help="待重入库的 *.md 目录")
    args = ap.parse_args()

    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    base = args.base_url.rstrip("/")

    files = sorted(Path(args.dir).glob("*.md"))
    if not files:
        print(f"目录无 .md 文件: {args.dir}")
        sys.exit(1)

    existing = requests.get(f"{base}/v1/documents", headers=headers, timeout=30).json()
    by_name = {d["name"]: d["id"] for d in existing}
    print(f"线上现有文档 {len(existing)} 份，本地待重入库 {len(files)} 份")

    for f in files:
        name = f.name
        if name in by_name:
            r = requests.delete(f"{base}/v1/documents/{by_name[name]}", headers=headers, timeout=30)
            print(f"  删除旧文档 {name} (id={by_name[name]}): {r.status_code}")
        content = f.read_text(encoding="utf-8")
        r = requests.post(
            f"{base}/v1/documents",
            headers=headers,
            json={"name": name, "content": content, "source_type": "file"},
            timeout=300,  # 入库含语义分块 embedding，大文档耗时较长
        )
        if r.status_code == 201:
            d = r.json()
            print(f"  重入库 {name}: id={d['id']} chunks={d['chunk_count']}")
        else:
            print(f"  ✗ 重入库失败 {name}: {r.status_code} {r.text[:200]}")
            sys.exit(1)

    print("完成。")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""复现 2026-08-26 线上 SOP 02 检索失败：用与生产相同的 chunker + embedding 重建
mod-dev-sop 13 份文档的向量索引，重放用户两轮问题，观察 02-数据英雄.md 各分块排名。

用法（在仓库根目录）：
  set QWEN_API_KEY=... && .venv/Scripts/python.exe testing/eval_rag/repro_sop02.py
"""
import io
import math
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.semantic_chunker import (  # noqa: E402
    contextualize_chunks,
    embed_texts_sync,
    semantic_chunk,
)

SOP_DIR = Path("D:/pycharm/pycharmprojects/teamfightmanager-OWmods/mod-dev-sop")
EXTRA_DOC = Path("D:/pycharm/pycharmprojects/teamfightmanager-OWmods/TeamfightManager2Mod/docs/data-champion.md")

QUERIES = [
    ("Q1-用户原话", "我希望为teamfightmanager2创建一个mod，该mod的功能是增添一个全新的英雄并且拥有独特的技能、立绘等机制，我该如何操作"),
    ("Q2-追问SOP02", "SOP 02 数据英雄 如何制作 .data_champion 新英雄的具体操作步骤"),
    ("Q3-具体字段", "数据英雄 .data_champion JSON 最小结构 字段 技能 效果 立绘 绑定"),
]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def build_corpus(extra=False, contextual=False):
    files = sorted(SOP_DIR.glob("*.md"))
    if extra:
        files.append(EXTRA_DOC)
    corpus = []  # (doc_name, position, chunk_text)
    for f in files:
        text = f.read_text(encoding="utf-8")
        chunks = semantic_chunk(text)
        # --contextual：模拟 2026-08-26 修复后的入库（块带 "[文档名 · 章节路径]" 前缀）
        if contextual:
            chunks = contextualize_chunks(chunks, f.name, text)
        print(f"[chunk] {f.name}: {len(text)} chars -> {len(chunks)} chunks, sizes={[len(c) for c in chunks]}")
        for i, c in enumerate(chunks):
            corpus.append((f.name, i, c))
    return corpus


def main():
    extra = "--extra" in sys.argv
    contextual = "--contextual" in sys.argv
    print(f"=== 构建 corpus (extra={extra}, contextual={contextual}) ===")
    corpus = build_corpus(extra=extra, contextual=contextual)
    print(f"total chunks: {len(corpus)}")

    chunk_vecs = embed_texts_sync([c for _, _, c in corpus])

    # 02-数据英雄.md 的分块索引集合
    sop02_positions = {i for i, (n, p, _) in enumerate(corpus) if n == "02-数据英雄.md"}

    for label, q in QUERIES:
        qv = embed_texts_sync([q])[0]
        scored = [(cosine(qv, cv), i) for i, cv in enumerate(chunk_vecs)]
        scored.sort(reverse=True)
        print(f"\n=== {label}: {q[:40]}... ===")
        print("--- top-10（修复后 rag_search 默认 top_k=10 的可见窗口） ---")
        for rank, (s, i) in enumerate(scored[:10], 1):
            name, pos, text = corpus[i]
            mark = " ◀ SOP02" if i in sop02_positions else ""
            nav = " (导航块)" if pos == 0 else ""
            preinject = " [预注入命中]" if s >= 0.65 else ""
            print(f"#{rank} sim={s:.3f} [{name}#p{pos}]{mark}{nav}{preinject} | {text[:60].replace(chr(10), ' ')}")
        ranks02 = [(r, s) for r, (s, i) in enumerate(scored, 1) if i in sop02_positions]
        print(f"--- 02-数据英雄.md 全部分块排名 ---")
        for r, s in ranks02:
            print(f"  rank={r} sim={s:.3f}")


if __name__ == "__main__":
    main()

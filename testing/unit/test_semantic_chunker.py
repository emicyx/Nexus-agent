"""语义分块单测（backend/app/services/semantic_chunker.py）。

用 mock 嵌入函数（按关键词返回正交向量），零网络依赖，验证：
- 空文本 / 短文本
- 主题断点切分 / 同主题合并
- max_chunk 兜底
- 句子级不中断
"""
from app.services.semantic_chunker import (
    semantic_chunk,
    _split_sentences,
    _is_markdown_table,
    _is_fenced_code_block,
)


def _mock_embed(texts: list[str]) -> list[list[float]]:
    """按关键词返回正交向量：ALPHA→[1,0,0]，BETA→[0,1,0]，其他→[0,0,1]。"""
    vecs = []
    for t in texts:
        if "ALPHA" in t:
            v = [1.0, 0.0, 0.0]
        elif "BETA" in t:
            v = [0.0, 1.0, 0.0]
        else:
            v = [0.0, 0.0, 1.0]
        vecs.append(v)
    return vecs


def _para(word: str, n: int = 60) -> str:
    """构造一段 4*n+len(word) 字符、包含 word 标记、以句号收尾的段落。"""
    return word + ("内容甲。" * n)


# ---------- 基础 ----------

def test_empty_text():
    assert semantic_chunk("", embed_fn=_mock_embed) == []
    assert semantic_chunk("  \n\n  ", embed_fn=_mock_embed) == []


def test_short_text_single_chunk():
    text = "这是一段很短的内容。"
    assert semantic_chunk(text, embed_fn=_mock_embed) == [text]


# ---------- 语义合并/切分 ----------

def test_split_at_topic_boundary():
    p1 = _para("ALPHA ")  # 关键词 A
    p2 = _para("BETA ")   # 关键词 B（不同主题）
    chunks = semantic_chunk(p1 + "\n\n" + p2, embed_fn=_mock_embed)
    assert len(chunks) == 2
    assert "ALPHA" in chunks[0] and "BETA" not in chunks[0]
    assert "BETA" in chunks[1]


def test_merge_same_topic():
    p1 = _para("ALPHA ")
    p2 = _para("ALPHA ")  # 同主题 → 合并为一块
    chunks = semantic_chunk(p1 + "\n\n" + p2, embed_fn=_mock_embed)
    assert len(chunks) == 1
    assert chunks[0].count("ALPHA") == 2


# ---------- 尺寸约束 ----------

def test_max_chunk_enforced():
    p1 = "ALPHA " + "内容甲" * 15   # ~51 字符
    p2 = "ALPHA " + "内容甲" * 15
    chunks = semantic_chunk(p1 + "\n\n" + p2, embed_fn=_mock_embed, max_chunk=100, min_chunk=50)
    assert len(chunks) == 2
    assert all(len(c) <= 100 for c in chunks)


def test_no_mid_sentence_cut():
    para = "第一句内容甲。" * 30   # 210 字符，30 个完整句
    chunks = semantic_chunk(para, embed_fn=_mock_embed, max_chunk=120, min_chunk=40)
    assert len(chunks) >= 2
    # 每个块都应以句末标点结尾（句子级保证，绝不句中断裂）
    for c in chunks:
        assert c.endswith(("。", "！", "？", "；")), f"块被句中断裂: ...{c[-30:]}"


# ---------- 句子切分 / 结构块 ----------

def test_split_sentences():
    sents = _split_sentences("第一句。第二句！第三句？\n第四句；第五句")
    assert sents == ["第一句。", "第二句！", "第三句？", "第四句；", "第五句"]


def test_markdown_table_kept_atomic():
    table = (
        "| 层 | 技术 | 说明 |\n"
        "|---|---|---|\n"
        "| 前端 | Next.js | UI |\n"
        "| 后端 | FastAPI | API |"
    )
    assert _is_markdown_table(table)
    # 表格整体作为一个原子单元，不按句子切
    chunks = semantic_chunk(table + "\n\n" + _para("ALPHA "), embed_fn=_mock_embed)
    assert any("| 前端 |" in c and "| 后端 |" in c for c in chunks), "表格被切开"


def test_code_block_kept_atomic():
    code = "```python\nx = 'a。b'  # 句号不切代码\n```"
    assert _is_fenced_code_block(code)
    chunks = semantic_chunk(code, embed_fn=_mock_embed)
    assert len(chunks) == 1 and "x = 'a。b'" in chunks[0]


# ---------- 分块上下文化（2026-08-26 SOP 02 事故修复） ----------

from app.services.semantic_chunker import (
    contextualize_chunks,
    _heading_spans,
    _heading_path_at,
)


def _sop_like_source() -> str:
    """模拟 SOP 文档结构：h1 标题 + h2 章节 + 正文段落 + h3 小节。"""
    return (
        "# SOP 02 · 数据英雄制作\n\n"
        "> 目标：用纯 JSON 添加一个可玩英雄。\n\n"
        "## 2. 最小可用结构\n\n"
        "attack、skill、skill2 由加载器强制要求；ult 可选。\n\n"
        "### 字段说明\n\n"
        "stat 是 1 级基础属性；growth 是每级成长。\n\n"
        "## 3. 标准开发顺序\n\n"
        "先复用基础游戏精灵，再补图标与文本。\n"
    )


def test_heading_spans_tracks_levels():
    spans = _heading_spans(_sop_like_source())
    # h1 → h2 → h3 → h2（层级回退后路径只剩 h1>h2）
    assert [p for _, p in spans] == [
        ["SOP 02 · 数据英雄制作"],
        ["SOP 02 · 数据英雄制作", "2. 最小可用结构"],
        ["SOP 02 · 数据英雄制作", "2. 最小可用结构", "字段说明"],
        ["SOP 02 · 数据英雄制作", "3. 标准开发顺序"],
    ]


def test_heading_spans_ignores_hash_in_code_fence():
    src = "## 真标题\n\n```\n# 这是代码注释不是标题\n```\n\n正文段落内容。\n"
    spans = _heading_spans(src)
    assert len(spans) == 1
    assert spans[0][1] == ["真标题"]


def test_contextualize_chunks_prefixes_heading_path():
    src = _sop_like_source()
    chunks = [
        "attack、skill、skill2 由加载器强制要求；ult 可选。",
        "stat 是 1 级基础属性；growth 是每级成长。",
        "先复用基础游戏精灵，再补图标与文本。",
    ]
    out = contextualize_chunks(chunks, "02-数据英雄.md", src)
    assert out[0].startswith("[02-数据英雄.md · SOP 02 · 数据英雄制作 > 2. 最小可用结构]\n")
    assert out[1].startswith(
        "[02-数据英雄.md · SOP 02 · 数据英雄制作 > 2. 最小可用结构 > 字段说明]\n"
    )
    assert out[2].startswith("[02-数据英雄.md · SOP 02 · 数据英雄制作 > 3. 标准开发顺序]\n")
    # 原文内容保留在前缀之后
    assert chunks[0] in out[0]


def test_contextualize_chunks_fallback_on_unlocatable_chunk():
    """块在原文中定位失败（如原文已改写）→ 仅文档名前缀，不丢块。"""
    out = contextualize_chunks(["完全不在原文里的内容。"], "d.md", "# 标题\n\n别的正文。")
    assert out == ["[d.md]\n完全不在原文里的内容。"]


def test_contextualize_chunks_empty():
    assert contextualize_chunks([], "d.md", "任意") == []


def test_contextualize_chunks_monotonic_matching():
    """重复开头文本时按文档顺序单调定位，不回退误配到前一处。"""
    src = "## A\n\n开头相同的内容。第一处。\n\n## B\n\n开头相同的内容。第二处。\n"
    out = contextualize_chunks(
        ["开头相同的内容。第一处。", "开头相同的内容。第二处。"], "d.md", src
    )
    assert " · A]" in out[0]
    assert " · B]" in out[1]

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

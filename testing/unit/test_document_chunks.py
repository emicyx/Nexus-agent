"""文档切块单测（backend/app/services/document_service.py `_split_chunks`）。"""
from app.services.document_service import _split_chunks


def test_empty_text():
    assert _split_chunks("") == []
    assert _split_chunks("  \n\n  ") == []


def test_split_by_paragraphs():
    text = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
    chunks = _split_chunks(text)
    assert len(chunks) == 3
    assert all(c.startswith("第") for c in chunks)


def test_hard_split_long_paragraph():
    long_para = "长" * 1200  # 超过 500 上限
    chunks = _split_chunks(long_para)
    assert len(chunks) == 3  # 1200 = 500 + 500 + 200
    assert all(len(c) <= 500 for c in chunks)


def test_mixed_paragraphs_and_strip():
    text = "短段。\n\n" + "长" * 1100
    chunks = _split_chunks(text)
    # 短段 1 条 + 长段硬切 3 条
    assert len(chunks) == 4
    assert "短段。" in chunks[0]
    assert all(len(c) <= 500 for c in chunks)

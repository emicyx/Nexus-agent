"""关键词 tsquery 构造单测（backend/app/services/keyword_search.py）。"""
from app.services.keyword_search import build_or_tsquery


def test_filters_stopwords():
    # '是'/'什么' 是停用词，应被过滤；其余保留
    tsq = build_or_tsquery(["nexus", "平台", "数据库", "是", "选型"])
    assert tsq is not None
    assert "是" not in tsq
    assert "数据库" in tsq and "选型" in tsq
    # OR 连接
    assert " | " in tsq


def test_returns_none_for_all_stopwords():
    assert build_or_tsquery(["是", "的", "什么"]) is None


def test_single_char_filtered():
    # 单字符词被过滤
    assert build_or_tsquery(["切", "块", "知识库"]) == "知识库"


def test_dedupe():
    tsq = build_or_tsquery(["检索", "检索", "知识库", "检索"])
    assert tsq.count("检索") == 1


def test_safe_terms_only():
    # 含非法字符的词元被跳过，不会破坏 to_tsquery 语法
    tsq = build_or_tsquery(["检索", "bad-term'!", "知识库"])
    assert tsq is not None
    assert "bad-term'!" not in tsq
    assert "检索" in tsq


def test_max_terms_by_length():
    # 超出 max_terms 时按词长降序保留
    lexemes = ["一二三四五六七八九十", "技术选型", "数据库", "平台", "检索", "架构", "工具", "版本", "审批", "配置"]
    tsq = build_or_tsquery(lexemes, max_terms=4)
    parts = tsq.split(" | ")
    assert len(parts) == 4
    assert parts[0] == "一二三四五六七八九十"  # 最长词优先保留

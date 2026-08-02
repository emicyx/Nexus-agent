"""关键词检索路 tsquery 构造（修复 plainto_tsquery 全 AND 导致的召回失效）

问题：
    plainto_tsquery('chinese', q) 把自然问题切出的所有 token 全部 AND
    （如 'nexus' & '平台' & '数据库' & '技术' & '选型' & '是'），要求分块
    同时含全部词（含"是/什么"等停用词），几乎无命中 → 关键词路形同虚设。

方案：
    用 zhparser 对查询切词（to_tsvector('chinese', q) 取 lexeme），过滤停用词后
    用 OR 连接（to_tsquery('chinese', 't1 | t2 | ...')）。ts_rank 按"命中词数/覆盖率"
    自然排序——覆盖查询词越多的分块排名越高，从而召回共享关键词的分块。
"""
from __future__ import annotations

import re

# 合法 tsquery 词元：小写字母 / 数字 / 下划线 / CJK，防止异常词元破坏 to_tsquery 语法
_SAFE_TERM_RE = re.compile(r"^[a-z0-9_\u4e00-\u9fff]+$")

# 中文 + 英文常见停用词（针对本项目技术问答场景）
STOPWORDS = {
    # 中文虚词/疑问词
    "是", "的", "了", "吗", "呢", "吧", "啊", "呀", "么",
    "什么", "怎么", "如何", "哪个", "哪些", "多少", "为什么", "怎样", "几",
    "一个", "一种", "这个", "那个", "这些", "那些", "请", "请问",
    "在", "与", "和", "及", "为", "于", "之", "其", "中", "对", "从", "到",
    "用", "进行", "以及", "关于", "叫", "说", "有", "要", "会", "可以", "能",
    "介绍", "说明", "情况", "内容", "问题", "相关", "方面",
    # 英文
    "a", "an", "the", "is", "are", "was", "were", "what", "how", "which",
    "why", "when", "where", "of", "for", "in", "on", "to", "at", "do", "does",
    "and", "or", "it", "its", "this", "that", "with", "by", "about",
}

# 关键词路最多保留的词数（超出按长度取信息量大的）
MAX_TSQUERY_TERMS = 8


def build_or_tsquery(lexemes: list[str], max_terms: int = MAX_TSQUERY_TERMS) -> str | None:
    """从 zhparser 切出的 lexemes 构造 OR tsquery 字符串。

    Args:
        lexemes: to_tsvector('chinese', q) 返回的词元列表
        max_terms: 最多保留的词数

    Returns:
        't1 | t2 | ...' 或 None（无有效内容词时）
    """
    # 过滤停用词 + 单字符词（多为虚词/噪声）+ 非法 tsquery 字符，去重
    terms: list[str] = []
    seen: set[str] = set()
    for w in lexemes:
        w = w.strip().lower()
        if not w or len(w) < 2 or w in STOPWORDS or w in seen:
            continue
        if not _SAFE_TERM_RE.match(w):
            continue
        seen.add(w)
        terms.append(w)
    if not terms:
        return None
    # 按词长降序取前 max_terms（较长词通常更具区分度）
    terms.sort(key=len, reverse=True)
    terms = terms[:max_terms]
    return " | ".join(terms)

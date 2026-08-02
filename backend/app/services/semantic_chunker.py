"""语义分块（句子级 + Embedding 相似度边界检测）

替代 document_service._split_chunks 的 500 字硬切方案，用于两条入库路径：
- document_service.ingest_document（直接上传 API）
- kb_ingest_tool（网页编排入库）

核心思想：
1. 以「句子」为最小原子单元，绝不拦腰截断句子（解决 500 字硬切的句中断裂问题）；
2. 对相邻原子单元做 Embedding 余弦相似度，相似度骤降处视为「主题断点」；
3. 结合 max_chunk / min_chunk 硬约束，输出主题连贯、尺寸可控的分块。

实现前提（2026-08-02 调研）：
- 环境无 langchain/llama_index/jieba 等分块库 → 基于 DashScope text-embedding-v3 自研，零新依赖；
- 实测相邻句相似度：同主题 0.59~0.71，主题切换 0.38~0.44 → 可用阈值/百分位区分；
- DashScope 兼容模式单请求最多 10 条输入 → 嵌入分批 10；
- 只对「超长段落」做句子级嵌入，短段落直接作为原子单元，控制嵌入成本。

同步实现（requests + psycopg2 兼容），供 document_service 用 asyncio.to_thread 调用、
供 kb_ingest_tool 在 worker 线程直接调用。
"""
from __future__ import annotations

import logging
import math
import os
from typing import Callable

import requests

from app.config import settings

logger = logging.getLogger("semantic_chunker")

_EMBED_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
_EMBED_BATCH = 10  # DashScope 兼容模式单次最多 10 条
_EMBED_TIMEOUT = 60

# 中文句末标点（含换行）——作为句子边界
_SENT_END_CHARS = "。！？…!?;；\n"

# 默认分块参数
DEFAULT_MAX_CHUNK = 800      # 块大小上限（字符）
DEFAULT_MIN_CHUNK = 150      # 块大小下限（过小则继续合并，避免碎片）
DEFAULT_SIM_FLOOR = 0.45     # 相似度下限：低于该值视为主题断点
DEFAULT_SIM_PERCENTILE = 30  # 自适应阈值取相似度分布的 30% 分位


# ---------- 基础工具 ----------

def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _vec_to_sql_literal(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


# ---------- 同步 Embedding（共享给两条入库路径） ----------

def embed_texts_sync(texts: list[str], batch_size: int = _EMBED_BATCH) -> list[list[float]]:
    """同步批量调用 DashScope embedding，按 batch_size 分批。

    Args:
        texts: 待嵌入文本列表（非空）
        batch_size: 每批条数，默认 10（DashScope 上限）

    Returns:
        list[list[float]]，与输入顺序一致的向量列表。
    """
    if not texts:
        return []
    api_key = os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError("缺少 API Key：请设置 QWEN_API_KEY 环境变量")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    all_embeddings: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        payload = {
            "model": settings.EMBEDDING_MODEL,
            "input": batch,
            "dimensions": settings.EMBEDDING_DIM,
            "encoding_format": "float",
        }
        r = requests.post(_EMBED_ENDPOINT, json=payload, headers=headers, timeout=_EMBED_TIMEOUT)
        if not r.ok:
            logger.error(
                "semantic_chunker embedding error status=%s batch=%d/%d body=%s",
                r.status_code, i // batch_size + 1, -(-len(texts) // batch_size), r.text[:300],
            )
        r.raise_for_status()
        data = r.json()["data"]
        all_embeddings.extend([d["embedding"] for d in data])
    return all_embeddings


# ---------- 句子切分 / 结构块识别 ----------

def _split_sentences(text: str) -> list[str]:
    """按中文句末标点 + 换行切分句子（保留标点）。

    代码块/表格等结构化内容应在调用前单独处理，不进入本函数。
    """
    if not text:
        return []
    sentences: list[str] = []
    buf: list[str] = []
    for ch in text:
        buf.append(ch)
        if ch in _SENT_END_CHARS:
            s = "".join(buf).strip()
            if s:
                sentences.append(s)
            buf = []
    tail = "".join(buf).strip()
    if tail:
        sentences.append(tail)
    return sentences


def _is_fenced_code_block(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def _is_markdown_table(text: str) -> bool:
    """判定连续管道符行构成的 markdown 表格块。"""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if len(lines) < 2:
        return False
    pipe_lines = sum(1 for l in lines if l.startswith("|"))
    return pipe_lines >= 2 and pipe_lines / len(lines) >= 0.8


def _is_structured_block(text: str) -> bool:
    """代码块 / 表格整体作为一个原子单元，不按句子切分。"""
    return _is_fenced_code_block(text) or _is_markdown_table(text)


# ---------- 语义分块主逻辑 ----------

def semantic_chunk(
    text: str,
    embed_fn: Callable[[list[str]], list[list[float]]] = embed_texts_sync,
    max_chunk: int = DEFAULT_MAX_CHUNK,
    min_chunk: int = DEFAULT_MIN_CHUNK,
    sim_floor: float = DEFAULT_SIM_FLOOR,
    sim_percentile: int = DEFAULT_SIM_PERCENTILE,
) -> list[str]:
    """句子级 Embedding 相似度语义分块。

    Args:
        text: 待切块文本
        embed_fn: 同步嵌入函数（默认 embed_texts_sync）
        max_chunk: 块大小上限（字符），超上限在句子边界兜底切
        min_chunk: 块大小下限，过小的块继续合并
        sim_floor: 相邻单元相似度下限（低于视为主题断点）
        sim_percentile: 自适应阈值取相似度分布的该分位

    Returns:
        list[str] 分块列表。
    """
    if not text or not text.strip():
        return []

    # 1) 段落分组（\n\n 为段落边界）
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    # 2) 构造原子单元：
    #    - 代码块 / 表格：整体一个单元（不切）
    #    - 短段落（<= max_chunk）：整体一个单元（不切）
    #    - 长段落（> max_chunk）：句子级切分，每句一个单元（超长句兜底硬切）
    units: list[str] = []
    for para in paragraphs:
        if _is_structured_block(para):
            units.append(para)
        elif len(para) <= max_chunk:
            units.append(para)
        else:
            sentences = _split_sentences(para)
            if not sentences:
                # 无任何句末标点的长文本：按 max_chunk 兜底硬切（极端情况）
                for i in range(0, len(para), max_chunk):
                    units.append(para[i : i + max_chunk])
            else:
                for s in sentences:
                    # 极少数单句超过 max_chunk（如无标点长串）：兜底切
                    for i in range(0, len(s), max_chunk):
                        units.append(s[i : i + max_chunk])

    if len(units) <= 1:
        return units if units else []

    # 3) 合并过短单元（< 40 字）为 ≥40 字的缓冲单元，避免短片段嵌入向量过噪
    buffered: list[str] = []
    buf = ""
    for u in units:
        buf = (buf + "\n\n" + u) if buf else u
        if len(buf) >= 40:
            buffered.append(buf)
            buf = ""
    if buf:
        buffered.append(buf)
    units = buffered
    if len(units) <= 1:
        return units if units else []

    # 4) 批量嵌入全部原子单元
    embeddings = embed_fn(units)
    if len(embeddings) != len(units):
        raise ValueError(f"语义分块嵌入数量不匹配：{len(embeddings)} != {len(units)}")

    # 5) 相邻相似度 + 自适应阈值
    sims = [_cosine(embeddings[i], embeddings[i + 1]) for i in range(len(embeddings) - 1)]
    if sims:
        sims_sorted = sorted(sims)
        idx = min(len(sims_sorted) - 1, max(0, int(len(sims_sorted) * sim_percentile / 100)))
        adaptive = sims_sorted[idx]
        threshold = max(sim_floor, adaptive)
        logger.debug("semantic_chunk: units=%d sim_percentile=%d adaptive=%.3f threshold=%.3f",
                     len(units), sim_percentile, adaptive, threshold)
    else:
        threshold = sim_floor

    # 6) 贪心合并：主题断点 + min/max 约束
    chunks: list[str] = []
    cur = units[0]
    cur_len = len(cur)
    for i in range(1, len(units)):
        u = units[i]
        is_breakpoint = sims[i - 1] < threshold
        # 断点处切，除非当前块还不够大（避免碎片）；max_chunk 永远兜底切
        if cur_len >= min_chunk and (is_breakpoint or cur_len + len(u) > max_chunk):
            chunks.append(cur)
            cur = u
            cur_len = len(u)
        else:
            cur = cur + "\n\n" + u
            cur_len = len(cur)
    chunks.append(cur)

    # 7) 兜底：确保没有任何块为空 / 全部非空
    return [c for c in chunks if c.strip()]

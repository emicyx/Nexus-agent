"""文档 RAG 服务（Week 4 + Week 9 混合检索）

- ingest_document: 切块 + 嵌入 + 写库
- search_documents: 向量 + 关键词 RRF 融合检索（SQL 与参数见 hybrid_search）
- list_documents / delete_document

Week 2026-08: 入库切块改为「句子级 Embedding 相似度语义分块」（semantic_chunker.semantic_chunk），
替代原 500 字硬切方案（原 _split_chunks 保留仅供单测/参考）。
"""
import asyncio
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.sql import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import vec_to_sql_literal
from app.llm.embedding import embed_query, embed_texts
from app.models import DocumentChunk, DocumentConfig
from app.services.hybrid_search import (
    HYBRID_SQL,
    LEXEME_SQL,
    build_tsquery,
    hybrid_params,
    map_rows,
)
from app.services.semantic_chunker import semantic_chunk

logger = logging.getLogger("document_service")

# 分块参数（保留旧参数供 _split_chunks 单测参考；入库实际用 semantic_chunk）
_CHUNK_MAX_CHARS = 500
_PARA_SEP = "\n\n"


def _split_chunks(text: str) -> list[str]:
    """按段落切块，单段超过上限时硬切。（旧方案，保留供单测/参考；入库改用 semantic_chunk）"""
    if not text:
        return []
    paragraphs = [p.strip() for p in text.split(_PARA_SEP) if p.strip()]
    chunks: list[str] = []
    for para in paragraphs:
        if len(para) <= _CHUNK_MAX_CHARS:
            chunks.append(para)
        else:
            # 硬切
            for i in range(0, len(para), _CHUNK_MAX_CHARS):
                chunks.append(para[i : i + _CHUNK_MAX_CHARS])
    return chunks


async def ingest_document(
    session: AsyncSession,
    name: str,
    content: str,
    source_type: str = "text",
) -> DocumentConfig:
    """语义切块 + 嵌入 + 写入文档与分块。

    语义切块（semantic_chunk）内部含同步 embedding 调用，用 asyncio.to_thread
    放到 worker 线程执行，不阻塞事件循环。
    """
    chunks_text = await asyncio.to_thread(semantic_chunk, content)
    if not chunks_text:
        raise ValueError("文档内容为空，无法切块")

    # 批量嵌入
    embeddings = await embed_texts(chunks_text)
    if len(embeddings) != len(chunks_text):
        raise ValueError(f"嵌入数量不匹配：{len(embeddings)} != {len(chunks_text)}")

    doc = DocumentConfig(
        name=name,
        source_type=source_type,
        content_text=content,
    )
    session.add(doc)
    await session.flush()  # 拿到 doc.id

    chunk_objs = [
        DocumentChunk(
            document_id=doc.id,
            content=txt,
            embedding=emb,
            position=idx,
            metadata_json={"source": name, "position": idx},
        )
        for idx, (txt, emb) in enumerate(zip(chunks_text, embeddings))
    ]
    session.add_all(chunk_objs)
    await session.commit()
    await session.refresh(doc)
    logger.info(
        "ingest_document: doc=%s chunks=%d dim=%d",
        doc.id, len(chunk_objs), len(embeddings[0]),
    )
    return doc


async def search_documents(
    session: AsyncSession,
    query: str,
    top_k: int = 5,
    document_id: int | None = None,
) -> list[dict[str, Any]]:
    """混合检索：向量 + 关键词 RRF 融合（SQL 见 hybrid_search.HYBRID_SQL）。

    向量路用 pgvector cosine_distance，关键词路用 zhparser 中文分词 ts_rank（OR tsquery）。
    两路各取前 RRF_POOL 条，用 RRF(k=60) 融合后取 top_k。

    可选 document_id 限定检索范围。

    返回 [{content, document_name, score(=rrf_score), position}]
    """
    if not query or not query.strip():
        return []
    q = query.strip()
    query_vec = await embed_query(q)
    vec_literal = vec_to_sql_literal(query_vec)

    lexemes = (await session.execute(LEXEME_SQL, {"q": q})).scalars().all()
    tsq = build_tsquery(list(lexemes))

    rows = (
        await session.execute(HYBRID_SQL, hybrid_params(vec_literal, tsq, document_id, top_k))
    ).all()
    return map_rows(rows)


async def list_documents(session: AsyncSession) -> list[dict[str, Any]]:
    """列出所有文档及其分块数。"""
    stmt = (
        select(
            DocumentConfig.id,
            DocumentConfig.name,
            DocumentConfig.source_type,
            DocumentConfig.created_at,
            func.count(DocumentChunk.id).label("chunk_count"),
        )
        .outerjoin(DocumentChunk, DocumentChunk.document_id == DocumentConfig.id)
        .group_by(DocumentConfig.id)
        .order_by(DocumentConfig.id)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "source_type": r.source_type,
            "created_at": r.created_at,
            "chunk_count": r.chunk_count,
        }
        for r in rows
    ]


async def delete_document(session: AsyncSession, doc_id: int) -> bool:
    """删除文档（级联删除分块）。"""
    doc = await session.get(DocumentConfig, doc_id)
    if doc is None:
        return False
    await session.delete(doc)
    await session.commit()
    return True


async def search_kb_high_confidence(
    query_vec: list[float],
    top_k: int = 2,
    score_threshold: float = 0.65,
) -> list[dict[str, Any]]:
    """纯语义检索（不用 hybrid），返回相似度 > threshold 的 top_k。

    用于 kickoff 前预注入相关知识库片段到 task description。
    cosine distance < (1 - threshold) 视为高置信。
    返回 [{"content", "document_name", "score"}]，score = 1 - distance。
    """
    if not query_vec:
        return []
    try:
        from app.db.session import AsyncSessionLocal
        vec_str = vec_to_sql_literal(query_vec)
        # 先取 3 倍候选，再按距离阈值过滤
        pool = top_k * 3
        stmt = sa_text(
            """
            SELECT dc.content, dc.position,
                   dc.embedding <=> CAST(:vec AS vector) AS distance,
                   doc.name AS document_name
            FROM document_chunks dc
            JOIN document_configs doc ON dc.document_id = doc.id
            ORDER BY dc.embedding <=> CAST(:vec AS vector)
            LIMIT :pool
            """
        )
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                stmt, {"vec": vec_str, "pool": pool}
            )).fetchall()

        max_distance = 1.0 - score_threshold
        results = []
        for r in rows:
            dist = float(r[2])
            if dist < max_distance:
                results.append({
                    "content": r[0],
                    "position": r[1],
                    "score": 1.0 - dist,
                    "document_name": r[3],
                })
            if len(results) >= top_k:
                break
        return results
    except Exception as e:
        logger.warning(f"search_kb_high_confidence failed: {e}")
        return []

